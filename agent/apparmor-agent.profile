#include <tunables/global>

profile containerclaw-agent flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  #include <abstractions/python>

  # Allow basic process management
  capability chown,
  capability dac_override,
  capability fowner,
  capability fsetid,
  capability kill,
  capability setgid,
  capability setuid,
  capability setpcap,
  capability net_bind_service,

  # Deny dangerous capabilities
  deny capability sys_admin,
  deny capability sys_module,
  deny capability sys_ptrace,
  deny capability sys_rawio,

  # Filesystem access
  # Read-only access to system binaries and libs (handled by base/python abstractions)
  
  # Workspace: read/write/lock
  /workspace/ r,
  /workspace/** rwk,

  # Tmpfs: read/write
  /tmp/ r,
  /tmp/** rwk,

  # Deny access to sensitive host paths
  deny /etc/shadow r,
  deny /etc/passwd r,
  deny /root/** rwx,
  deny /home/*/.ssh/** rwx,
  deny /home/*/.aws/** rwx,

  # Network: Allow binding to gRPC port, but restricted by Docker network policy
  network inet stream,
  network inet6 stream,
}
