# Live Colab CPU runtime probe — 2026-10-16

Notebook created and executed through the user's Chrome session:
https://colab.research.google.com/drive/1BqoOfEZpzlxSUpZEleQHO5K-AVyXkwkw

Environment reported: Linux 6.6.122+, x86_64, glibc 2.39, uid=0; CPU runtime.
No Drive mount, API tokens or inference. Installed Ubuntu docker.io 29.1.3.

1. Standard dockerd failed at bridge/NAT setup:
   `failed to create NAT chain DOCKER ... iptables ... Permission denied`.
2. Daemon with bridge/iptables/forwarding disabled started. Official python:3.12-slim
   downloaded with digest `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
   Restricted container failed: overlay mount `invalid argument`, exit 125.
3. Separate daemon with VFS and containerd-snapshotter=false started. Same official image pulled.
   Container retained `--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges
   --pids-limit=128 --memory=1g --cpus=2 --user=65534:65534`.
   Runtime rejected it:

```text
unable to apply cgroup configuration: mkdir /sys/fs/cgroup/docker: read-only file system
VFS_CONTAINER_EXIT 125
```

Conclusion scoped to this runtime: the proposed restricted container profile is UNAVAILABLE.
Docker client/daemon installation alone is not sufficient. No attempt to remove resource limits,
enable privileged containers, disable seccomp or remount cgroups was made.

The full current source bundle was NOT executed in Colab. Initial notebook upload failed because
Chrome extension file URL access is disabled. The environment probe was entered through the
visible notebook editor instead. Prepared full notebook remains available locally under artifacts/colab.
Local Windows test evidence is separate: 151 passed, 4 skipped plus 17 additional tests passed.
