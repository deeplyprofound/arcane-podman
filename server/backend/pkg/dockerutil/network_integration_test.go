//go:build integration

// Integration gate for the default-network drift (D2/D3). Point DOCKER_HOST at
// a real engine or the simulator and run with -tags integration.
//
// The book-based audit claimed Podman's default network is named "podman" and
// that IsDefaultNetwork misses it. The REAL compat API (verified live + in the
// simulator) aliases the default netavark network to "bridge", which
// IsDefaultNetwork already recognizes — so D2/D3 are moot over the compat API
// arcane actually talks to. This gate guards that: if a future Podman stops
// aliasing, it fails and we revisit.
package docker

import (
	"os"
	"testing"

	"github.com/moby/moby/client"
	"github.com/stretchr/testify/require"
)

func TestIntegration_DefaultNetworkRecognized(t *testing.T) {
	host := os.Getenv("DOCKER_HOST")
	if host == "" {
		t.Skip("DOCKER_HOST not set; run via `just test-sim` or a live engine")
	}

	probe, err := client.New(client.WithHost(host))
	require.NoError(t, err)
	defer probe.Close()
	ping, err := probe.Ping(t.Context(), client.PingOptions{})
	require.NoError(t, err, "engine unreachable at %s", host)
	apiVersion := ping.APIVersion
	if apiVersion == "" {
		apiVersion = client.MinAPIVersion
	}
	cli, err := client.New(client.WithHost(host), client.WithAPIVersion(apiVersion))
	require.NoError(t, err)
	defer cli.Close()

	res, err := cli.NetworkList(t.Context(), client.NetworkListOptions{})
	require.NoError(t, err)

	var names []string
	recognized := false
	for _, n := range res.Items {
		names = append(names, n.Name)
		if IsDefaultNetwork(n.Name) {
			recognized = true
		}
	}
	t.Logf("networks: %v", names)
	require.True(t, recognized, "engine must expose a default network arcane's IsDefaultNetwork recognizes (got %v)", names)

	if os.Getenv("ARCANE_EXPECT_PODMAN") != "" {
		// The whole point of the D2/D3 correction: compat aliases to "bridge".
		require.Contains(t, names, "bridge", "Podman compat API should alias its default network to 'bridge'")
	}
}
