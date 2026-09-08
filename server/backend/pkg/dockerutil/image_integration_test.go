//go:build integration

// Integration gate for short-name resolution (B7/B7b). Point DOCKER_HOST at the
// simulator or a real engine and run with -tags integration.
//
// The book-based audit said Podman won't implicitly prepend docker.io, so
// clients must normalize short names. The REAL compat /images/create DOES
// default unqualified names to docker.io/library (verified live) AND preserves
// explicit registries — so arcane must NOT normalize (it would override the
// user's registries.conf). This gate guards that behavior.
package docker

import (
	"io"
	"os"
	"testing"

	"github.com/moby/moby/client"
	"github.com/stretchr/testify/require"
)

func TestIntegration_CompatResolvesShortNames(t *testing.T) {
	host := os.Getenv("DOCKER_HOST")
	if host == "" {
		t.Skip("DOCKER_HOST not set; run via `just test-sim`")
	}
	probe, err := client.New(client.WithHost(host))
	require.NoError(t, err)
	defer probe.Close()
	ping, err := probe.Ping(t.Context(), client.PingOptions{})
	require.NoError(t, err)
	api := ping.APIVersion
	if api == "" {
		api = client.MinAPIVersion
	}
	cli, err := client.New(client.WithHost(host), client.WithAPIVersion(api))
	require.NoError(t, err)
	defer cli.Close()

	pull := func(ref string) {
		rc, err := cli.ImagePull(t.Context(), ref, client.ImagePullOptions{})
		require.NoError(t, err, "pull %s", ref)
		_, _ = io.Copy(io.Discard, rc)
		_ = rc.Close()
	}
	pull("busybox")              // single segment -> docker.io/library/…
	pull("grafana/grafana")      // 2-segment, no registry -> docker.io/…
	pull("myreg.io/team/app:v1") // explicit registry -> preserved

	res, err := cli.ImageList(t.Context(), client.ImageListOptions{All: true})
	require.NoError(t, err)
	var tags []string
	for _, img := range res.Items {
		tags = append(tags, img.RepoTags...)
	}
	t.Logf("image tags: %v", tags)

	require.Contains(t, tags, "docker.io/library/busybox:latest", "compat should default a single-segment name to docker.io/library")
	require.Contains(t, tags, "docker.io/grafana/grafana:latest", "compat should default a registry-less name to docker.io")
	require.Contains(t, tags, "myreg.io/team/app:v1")
	// The explicit registry must NOT be rewritten to docker.io — that would
	// override the user's registries.conf. (Guards against a wrong B7b "fix".)
	for _, tg := range tags {
		require.NotEqual(t, "docker.io/library/myreg.io/team/app:v1", tg)
	}
}
