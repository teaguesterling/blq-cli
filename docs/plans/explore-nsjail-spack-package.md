# Exploration: Spack Package for nsjail

*A prompt for exploring whether and how to create a Spack package for Google's nsjail.*

## Context

[nsjail](https://github.com/google/nsjail) is Google's process isolation tool — it provides namespace isolation, cgroup resource limits, seccomp filtering, and declarative protobuf config files. It's the ideal enforcement backend for blq's sandbox spec system, but it's not available via standard package managers (no apt package, no pip package).

blq currently uses bubblewrap (bwrap) for namespace isolation. bwrap covers network, filesystem, PID, and tmpfs isolation, but lacks:
- Built-in cgroup management (memory/CPU limits)
- Seccomp filter support (syscall allowlisting via Kafel)
- Declarative config format (nsjail uses protobuf configs)
- Per-process cgroup creation/cleanup

[Spack](https://spack.io/) is a package manager for scientific computing that can build and install software from source with dependency resolution. It's well-suited for tools like nsjail that aren't in standard repos.

## Questions to Explore

### 1. Feasibility
- What are nsjail's build dependencies? (protobuf, libnl, Kafel submodule)
- Does Spack already have packages for these dependencies?
- Are there any kernel version requirements? (cgroup v2, user namespaces)
- Does nsjail require root to build? To run?

### 2. Spack Package Design
- What would the Spack recipe (`package.py`) look like?
- How to handle the Kafel submodule (bundled vs separate package)?
- What variants make sense? (e.g., `+kafel` for seccomp support)
- Should the package include the protobuf config schemas?

### 3. Distribution
- Where should the Spack package live? (upstream spack/spack repo, or a custom Spack repo?)
- How to handle versioning? (nsjail doesn't have frequent releases — may need to track git commits)
- Would this benefit other users? (security researchers, HPC, CI systems)

### 4. Integration with blq
- Once installed via Spack, how does blq discover nsjail? (`shutil.which("nsjail")`)
- Should blq's sandbox engine system prefer nsjail over bwrap when both are available?
- How to handle the case where nsjail is installed but lacks certain capabilities?

## Research Steps

1. Clone nsjail and examine its build system (Makefile, dependencies)
2. Check Spack's existing packages for protobuf, libnl-route-3 (`spack list protobuf`, `spack list libnl`)
3. Look at similar Spack packages for C++ tools with submodules (firejail, bubblewrap)
4. Write a draft `package.py` and test the build
5. Verify the built nsjail works with basic isolation tests

## References

- nsjail source: https://github.com/google/nsjail
- nsjail build instructions: https://github.com/google/nsjail#building
- Kafel (seccomp policy language): https://github.com/google/kafel
- Spack packaging guide: https://spack.readthedocs.io/en/latest/packaging_guide.html
- nsjail config.proto: https://github.com/google/nsjail/blob/master/config.proto
