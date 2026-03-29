# Exploration: Python Wrapper for nsjail

*A prompt for exploring a Python package that wraps nsjail's protobuf config and execution.*

## Context

nsjail uses protobuf config files to declare sandbox specifications. Our `SandboxSpec` dataclass already represents the same concepts (network, filesystem, timeout, memory, cpu, processes, tmpfs, paths). A Python wrapper would:

1. **Translate `SandboxSpec` → nsjail protobuf config** — generate the `.cfg` file nsjail expects
2. **Run nsjail programmatically** — wrap subprocess calls with proper argument handling
3. **Read cgroup stats before cleanup** — nsjail cleans up cgroups on exit, so we need to read memory.peak/cpu.stat before that happens
4. **Parse nsjail output** — exit codes, violation reports, seccomp logs

This could be a standalone PyPI package (`pynsjail` or `nsjail-python`) that blq depends on, or it could live inside `blq_sandbox_nsjail`.

## Questions to Explore

### 1. Config Translation
- What does nsjail's `config.proto` look like? Map each field to our `SandboxSpec` dimensions.
- Which nsjail config fields have no `SandboxSpec` equivalent? (rlimits? UID mapping? hostname?)
- Which `SandboxSpec` fields have no direct nsjail equivalent?
- Should we generate protobuf configs or use nsjail's CLI flags? (CLI is simpler but less capable)

### 2. Key Config Mappings

Expected mapping (verify against actual config.proto):

| SandboxSpec | nsjail config.proto | Notes |
|------------|-------------------|-------|
| `network = "none"` | `clone_newnet: true` | Full network namespace isolation |
| `network = "localhost"` | `clone_newnet: true` + loopback setup | Need to verify loopback config |
| `filesystem = "readonly"` | `mount { src: "/" dst: "/" is_bind: true rw: false }` | Read-only root |
| `filesystem = "workspace_only"` | Root ro + workspace rw mount | Two mount entries |
| `timeout` | `time_limit` | Seconds |
| `memory` | `cgroup_mem_max` | Bytes |
| `cpu` | `cgroup_cpu_ms_per_sec` | Different unit — CPU milliseconds per second |
| `processes = "isolated"` | `clone_newpid: true` | PID namespace |
| `tmpfs` | `mount { dst: "/tmp" fstype: "tmpfs" options: "size=..." }` | Tmpfs with size |
| `paths_hidden` | Omit from mount list | Don't bind-mount hidden paths |
| `paths_readable` | Additional ro bind mounts | Explicit read paths |

### 3. Cgroup Stats Recovery
- nsjail cleans up cgroups when the sandboxed process exits
- Options to read stats before cleanup:
  - Patch nsjail to delay cleanup (`--keep_env` or similar)
  - Use `--cgroup_parent` to place in a known parent cgroup we control
  - Read stats from within the sandbox via a wrapper script
  - Use the systemd engine alongside nsjail (systemd creates the cgroup, nsjail uses it)

### 4. Seccomp Integration
- nsjail uses Kafel for seccomp policy language
- Default policy options: `DEFAULT ALLOW`, `DEFAULT KILL`, `DEFAULT LOG`
- For Phase 0 Tier 3 (learning mode): `seccomp_log: true` + `DEFAULT LOG`
- For enforcement: generate allowlist from learned syscalls
- How to parse audit log output for learned syscalls?

### 5. Package Design

**Option A: Standalone `pynsjail` package**
```python
from pynsjail import NsjailConfig, run_nsjail

config = NsjailConfig()
config.clone_newnet = True
config.mount_readonly("/")
config.mount_readwrite("/workspace")
config.time_limit = 60
config.cgroup_mem_max = 512 * 1024 * 1024

result = run_nsjail(config, command=["pytest", "tests/"])
print(result.exit_code, result.cgroup_stats)
```

**Option B: blq-specific `blq_sandbox_nsjail` engine**
```python
class NsjailEngine:
    name = "nsjail"
    capabilities = {"network", "filesystem", "processes", "memory", "cpu", "tmpfs", "seccomp"}

    def wrap(self, command, spec, workspace, attempt_id):
        config = spec_to_nsjail_config(spec, workspace)
        config_path = write_config(config, attempt_id)
        return f"nsjail --config {config_path} -- {command}"
```

**Recommendation**: Start with Option B (blq-specific engine), extract to standalone package later if there's demand.

### 6. Testing Strategy
- Unit tests: config generation from SandboxSpec (no nsjail needed)
- Integration tests: run nsjail with generated configs (needs nsjail installed)
- Compatibility tests: verify against different nsjail versions
- Skip tests gracefully when nsjail is not available

## Research Steps

1. Fetch and read nsjail's `config.proto` — understand every field
2. Map `SandboxSpec` fields to config.proto fields — identify gaps
3. Build a minimal config generator in Python
4. Test with a real nsjail installation (may need to build from source first)
5. Investigate cgroup stat recovery approaches
6. Write a proof-of-concept `NsjailEngine` that passes the existing bwrap integration tests

## References

- nsjail config.proto: https://github.com/google/nsjail/blob/master/config.proto
- Kafel language: https://github.com/google/kafel
- nsjail usage docs: https://github.com/google/nsjail/blob/master/README.md
- blq SandboxSpec: `src/blq_sandbox/spec.py`
- blq BwrapEngine (reference): `src/blq_sandbox_bwrap/`
- blq engine protocol: `src/blq_sandbox/engines.py`
- Existing sandbox design doc: `docs/design/design-sandbox-specs.md`
