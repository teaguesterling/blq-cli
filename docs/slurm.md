# Slurm Routing

blq can run commands through a Slurm queue with `srun`. Output still streams to blq, so the run is captured and queryable as usual.

This guide uses two terms:

- **Slurm route**: blq runs the command as one Slurm job.
- **Local route**: blq runs the command as a local process.

With no policy file, blq uses the local route and does not call any Slurm tool.

## Policy File

The policy file is `~/.config/blq/slurm.toml`. It applies to all projects for this user.

```toml
roots = ["~/code"]
partition = "batch"
cpus = 4
mem = "8G"
local_memory_max = "16G"
```

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Set to `false` to turn off all Slurm routing. |
| `roots` | `[]` | Directories whose commands use the Slurm route. |
| `partition` | `"batch"` | The Slurm partition for each job. |
| `cpus` | `4` | The CPUs for each job. This must be an integer above 0. |
| `mem` | `"8G"` | The memory for each job, for example `512M` or `16G`. |
| `local_memory_max` | not set | The memory limit for commands on the local route. |

If the file has a bad value, blq writes a warning and ignores the whole file. The command then uses the local route.

## Roots

A command uses the Slurm route only if its working directory is in one of the `roots`. Subdirectories count. A `~` at the start of a root expands to the home directory.

## Settings for One Command

Add a `slurm` value to a command in `.bird/commands.toml`.

Use `slurm = false` to keep a command on the local route:

```toml
[commands.serve]
cmd = "npm run dev"
slurm = false
```

Use a table to change the resources of a command. A key that you do not set comes from the policy file:

```toml
[commands.build]
cmd = "make -j8"
slurm = { cpus = 8, mem = "16G", partition = "batch" }
```

If the value is not valid, blq writes a warning and ignores the value. The command still loads. `blq commands register --force` keeps the existing `slurm` value.

## Turn Off Routing for One Run

Set `BLQ_SLURM=0` to use the local route. The values `false`, `no` and `off` have the same effect.

```bash
BLQ_SLURM=0 blq run build
```

A command that blq starts inside a Slurm job also uses the local route.

## When the Queue Is Down

Before each run on the Slurm route, blq runs `scontrol ping`. If the check fails, blq writes this line to stderr and uses the local route:

```text
[blq] Slurm unavailable, running locally: <reason>
```

If `srun` then cannot contact the controller, blq writes the same warning and runs the command again on the local route. This rerun occurs only if the command did not start. Captured runs store the warning in their output. Runs with `--no-capture` do not have this rerun.

## Limits

These limits apply on the Slurm route:

- **Stdin is empty.** The command cannot read input from the terminal or from a pipe.
- **Background daemons die with the job.** A process that the command leaves behind stops when the job ends.
- **Interactive commands need `slurm = false`.** These commands need a terminal and stdin.
- **GPU and NPU commands need `slurm = false`.** blq does not request these devices from Slurm.
- **A timeout includes the time in the queue.** A job that waits in the queue can use all of its timeout before it starts.
- **Signal exit codes appear as 128+N.** For example, a command that receives SIGSEGV gives 139, and SIGKILL gives 137. They do not appear as negative numbers.

This limit applies on the local route:

- **A memory kill can show two results.** A command above `local_memory_max` shows `Terminated` with exit code 143, or `Killed` with exit code 137.
