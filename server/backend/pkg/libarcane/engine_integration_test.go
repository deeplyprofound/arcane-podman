//go:build integration

// Integration guardrails that run against a REAL container engine. Point
// DOCKER_HOST at a running engine (e.g. rootless Podman via `podman machine
// start`) and run:
//
//	DOCKER_HOST=unix://… go test -tags integration ./pkg/libarcane/ -run Integration -v
//
// Skips cleanly when DOCKER_HOST is unset so unit runs are unaffected.
package libarcane

import (
	"os"
	"syscall"
	"testing"

	"github.com/moby/moby/client"
	"github.com/stretchr/testify/require"
)

func newIntegrationClient(t *testing.T) (*client.Client, string) {
	t.Helper()
	host := os.Getenv("DOCKER_HOST")
	if host == "" {
		t.Skip("DOCKER_HOST not set; start `podman machine` and export DOCKER_HOST")
	}

	// Mirror the app's negotiation: ping for the advertised API version, then
	// pin the client to it (validates the Docker-compat floor claim).
	probe, err := client.New(client.WithHost(host))
	require.NoError(t, err)
	defer probe.Close()

	ping, err := probe.Ping(t.Context(), client.PingOptions{})
	require.NoError(t, err, "engine unreachable at %s", host)
	apiVersion := ping.APIVersion
	if apiVersion == "" {
		apiVersion = client.MinAPIVersion
	}
	t.Logf("negotiated API version: %s (MinAPIVersion=%s)", apiVersion, client.MinAPIVersion)

	cli, err := client.New(client.WithHost(host), client.WithAPIVersion(apiVersion))
	require.NoError(t, err)
	return cli, host
}

// Phase 1: engine capability detection against a real engine.
func TestIntegration_DetectEngineInfo(t *testing.T) {
	cli, _ := newIntegrationClient(t)
	defer cli.Close()

	info, err := DetectEngineInfo(t.Context(), cli)
	require.NoError(t, err)
	t.Logf("detected engine=%q cgroupVersion=%q podman=%v", info.Name, info.CgroupVersion, info.IsPodman())

	// Any real engine must resolve to a known name.
	require.Contains(t, []string{"podman", "docker"}, info.Name, "engine name should be detected")

	// When pointed at Podman (the fork's target), assert the podman path.
	if os.Getenv("ARCANE_EXPECT_PODMAN") != "" {
		require.Equal(t, "podman", info.Name)
		require.True(t, info.IsPodman())
		require.Equal(t, "2", info.CgroupVersion, "rootless Podman requires cgroups v2")
	}
}

// B1 premise: a rootless engine socket is owned by a NON-root user, which is
// exactly why the forced UID-65532 re-exec breaks and why Phase 2 runs as the
// socket owner instead. This verifies the ownership assumption against the real
// socket (unix socket paths only).
func TestIntegration_SocketOwnershipIsNonRootForRootless(t *testing.T) {
	_, host := newIntegrationClient(t)
	if os.Getenv("ARCANE_EXPECT_PODMAN") == "" {
		t.Skip("set ARCANE_EXPECT_PODMAN=1 to assert rootless socket ownership")
	}

	const prefix = "unix://"
	if len(host) <= len(prefix) || host[:len(prefix)] != prefix {
		t.Skipf("DOCKER_HOST %q is not a unix socket", host)
	}
	socketPath := host[len(prefix):]

	fi, err := os.Stat(socketPath)
	require.NoError(t, err)
	stat, ok := fi.Sys().(*syscall.Stat_t)
	require.True(t, ok)
	t.Logf("socket %s owned by uid=%d gid=%d", socketPath, stat.Uid, stat.Gid)
	require.NotEqual(t, uint32(0), stat.Uid, "rootless engine socket must be non-root-owned (drives Phase 2 identity)")
}
