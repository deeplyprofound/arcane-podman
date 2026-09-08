package enginesocket

import (
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

// statFor returns a stat function that reports success only for the given set
// of paths, and os.ErrNotExist for everything else. The FileInfo is unused by
// the resolver (it only checks err == nil), so nil is fine.
func statFor(existing ...string) func(string) (os.FileInfo, error) {
	set := make(map[string]struct{}, len(existing))
	for _, p := range existing {
		set[p] = struct{}{}
	}
	return func(path string) (os.FileInfo, error) {
		if _, ok := set[path]; ok {
			return nil, nil
		}
		return nil, os.ErrNotExist
	}
}

func envFor(pairs map[string]string) func(string) string {
	return func(key string) string { return pairs[key] }
}

const dockerDefault = "unix:///var/run/docker.sock"

func TestResolveContainerHost(t *testing.T) {
	tests := []struct {
		name         string
		explicit     bool
		current      string
		existing     []string
		env          map[string]string
		wantResolved string
		wantSource   string
	}{
		{
			name:         "explicit DOCKER_HOST is respected even if podman socket exists",
			explicit:     true,
			current:      "tcp://docker-socket-proxy:2375",
			existing:     []string{podmanRootfulSocketPath},
			wantResolved: "tcp://docker-socket-proxy:2375",
			wantSource:   SourceEnv,
		},
		{
			name:         "docker socket present keeps current unchanged",
			current:      dockerDefault,
			existing:     []string{dockerSocketPath},
			wantResolved: dockerDefault,
			wantSource:   SourceDocker,
		},
		{
			name:         "docker alias /run/docker.sock also keeps current",
			current:      dockerDefault,
			existing:     []string{dockerSocketPathAlias},
			wantResolved: dockerDefault,
			wantSource:   SourceDocker,
		},
		{
			name:         "docker wins over podman when both present",
			current:      dockerDefault,
			existing:     []string{dockerSocketPath, podmanRootfulSocketPath},
			wantResolved: dockerDefault,
			wantSource:   SourceDocker,
		},
		{
			name:         "rootful podman detected when no docker socket",
			current:      dockerDefault,
			existing:     []string{podmanRootfulSocketPath},
			wantResolved: "unix://" + podmanRootfulSocketPath,
			wantSource:   SourcePodmanRootful,
		},
		{
			name:         "rootless podman detected via XDG_RUNTIME_DIR",
			current:      dockerDefault,
			existing:     []string{"/run/user/1000/podman/podman.sock"},
			env:          map[string]string{"XDG_RUNTIME_DIR": "/run/user/1000"},
			wantResolved: "unix:///run/user/1000/podman/podman.sock",
			wantSource:   SourcePodmanRootless,
		},
		{
			name:         "rootful podman preferred over rootless when both present",
			current:      dockerDefault,
			existing:     []string{podmanRootfulSocketPath, "/run/user/1000/podman/podman.sock"},
			env:          map[string]string{"XDG_RUNTIME_DIR": "/run/user/1000"},
			wantResolved: "unix://" + podmanRootfulSocketPath,
			wantSource:   SourcePodmanRootful,
		},
		{
			name:         "podman alias path detected",
			current:      dockerDefault,
			existing:     []string{podmanRootfulPathAlias},
			wantResolved: "unix://" + podmanRootfulPathAlias,
			wantSource:   SourcePodman,
		},
		{
			name:         "nothing detected leaves current unchanged",
			current:      dockerDefault,
			existing:     nil,
			wantResolved: dockerDefault,
			wantSource:   SourceDefault,
		},
		{
			name:         "rootless ignored when XDG_RUNTIME_DIR unset",
			current:      dockerDefault,
			existing:     []string{"/run/user/1000/podman/podman.sock"},
			env:          nil,
			wantResolved: dockerDefault,
			wantSource:   SourceDefault,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resolved, source := ResolveContainerHost(
				tt.explicit, tt.current, statFor(tt.existing...), envFor(tt.env))
			require.Equal(t, tt.wantResolved, resolved)
			require.Equal(t, tt.wantSource, source)
		})
	}
}
