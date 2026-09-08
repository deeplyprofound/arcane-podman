package volumehelper

import (
	"testing"

	"github.com/getarcaneapp/arcane/backend/v2/pkg/libarcane"
	"github.com/moby/moby/api/types/mount"
	"github.com/stretchr/testify/require"
)

func TestHostConfig_SELinuxRelabelsHostBinds(t *testing.T) {
	podmanSel := libarcane.EngineCompatibilityInfo{Name: "podman", SELinuxEnabled: true}
	docker := libarcane.EngineCompatibilityInfo{Name: "docker"}

	hostMount := mount.Mount{Type: mount.TypeBind, Source: "/srv/backups", Target: "/backups", ReadOnly: true}
	volMount := mount.Mount{Type: mount.TypeVolume, Source: "namedvol", Target: "/data"}

	t.Run("podman+selinux converts host bind to :z Binds string, keeps volume mount", func(t *testing.T) {
		hc := HostConfig("img", nil, []mount.Mount{hostMount, volMount}, podmanSel)
		require.Contains(t, hc.Binds, "/srv/backups:/backups:ro,z")
		// the host bind is moved out of Mounts; the named volume stays
		require.Len(t, hc.Mounts, 1)
		require.Equal(t, mount.TypeVolume, hc.Mounts[0].Type)
	})

	t.Run("podman+selinux relabels host-path bind STRINGS but not named volumes", func(t *testing.T) {
		hc := HostConfig("img", []string{"/srv/data:/data", "namedvol:/vol"}, nil, podmanSel)
		require.Contains(t, hc.Binds, "/srv/data:/data:z")
		require.Contains(t, hc.Binds, "namedvol:/vol")
	})

	t.Run("docker leaves everything unchanged", func(t *testing.T) {
		hc := HostConfig("img", []string{"/srv/data:/data"}, []mount.Mount{hostMount, volMount}, docker)
		require.Equal(t, []string{"/srv/data:/data"}, hc.Binds)
		require.Len(t, hc.Mounts, 2)
	})
}
