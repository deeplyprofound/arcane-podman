// Package enginesocket resolves which container-engine socket Arcane should
// talk to. Podman exposes a Docker-compatible API socket, so when the user has
// not pinned DOCKER_HOST, Arcane auto-detects a running engine socket —
// preferring Docker for backward compatibility, then falling back to Podman's
// rootful and rootless socket paths.
package enginesocket

import (
	"os"
	"path/filepath"
	"strings"
)

// Well-known socket paths, probed in priority order. Docker first so existing
// Docker users see no behavior change; Podman paths only win when no Docker
// socket is present.
const (
	dockerSocketPath        = "/var/run/docker.sock"
	dockerSocketPathAlias   = "/run/docker.sock"
	podmanRootfulSocketPath = "/run/podman/podman.sock"
	podmanRootfulPathAlias  = "/var/run/podman/podman.sock"

	// podmanRootlessSocketRelPath is joined onto $XDG_RUNTIME_DIR to locate the
	// rootless Podman API socket (e.g. /run/user/1000/podman/podman.sock).
	podmanRootlessSocketRelPath = "podman/podman.sock"
)

// Sources returned by ResolveContainerHost, describing why a host was chosen.
const (
	SourceEnv             = "env"             // DOCKER_HOST was set by the user
	SourceDocker          = "docker"          // Docker socket present; kept the current value
	SourcePodmanRootful   = "podman-rootful"  // detected /run/podman/podman.sock
	SourcePodmanRootless  = "podman-rootless" // detected $XDG_RUNTIME_DIR/podman/podman.sock
	SourcePodman          = "podman"          // detected a Podman alias path
	SourceDefault         = "default"         // nothing detected; left the current value unchanged
)

// ResolveContainerHost returns the container-engine host URI Arcane should use.
//
// explicitlySet reports whether the user set DOCKER_HOST in the environment;
// when true the current value is always returned unchanged. current is the host
// already loaded from config (the DOCKER_HOST value, or the Docker default).
// statFn and getenvFn are injected so the resolver is testable without touching
// the real filesystem or environment.
//
// Detection is path-existence only (no connection attempt); validating that the
// socket actually speaks the Docker API stays with the Docker client's ping.
func ResolveContainerHost(
	explicitlySet bool,
	current string,
	statFn func(string) (os.FileInfo, error),
	getenvFn func(string) string,
) (resolved string, source string) {
	if explicitlySet {
		return current, SourceEnv
	}

	if statFn == nil {
		statFn = os.Stat
	}
	if getenvFn == nil {
		getenvFn = os.Getenv
	}

	exists := func(path string) bool {
		if path == "" {
			return false
		}
		_, err := statFn(path)
		return err == nil
	}

	// Docker first — keep the configured value untouched so existing setups are
	// unaffected.
	if exists(dockerSocketPath) || exists(dockerSocketPathAlias) {
		return current, SourceDocker
	}

	// Rootful Podman.
	if exists(podmanRootfulSocketPath) {
		return unixURI(podmanRootfulSocketPath), SourcePodmanRootful
	}

	// Rootless Podman under $XDG_RUNTIME_DIR (e.g. /run/user/1000/podman/podman.sock).
	if runtimeDir := strings.TrimSpace(getenvFn("XDG_RUNTIME_DIR")); runtimeDir != "" {
		rootless := filepath.Join(runtimeDir, podmanRootlessSocketRelPath)
		if exists(rootless) {
			return unixURI(rootless), SourcePodmanRootless
		}
	}

	// Podman alias path.
	if exists(podmanRootfulPathAlias) {
		return unixURI(podmanRootfulPathAlias), SourcePodman
	}

	// Nothing detected — leave the current value as-is (unchanged behavior).
	return current, SourceDefault
}

func unixURI(socketPath string) string {
	return "unix://" + socketPath
}
