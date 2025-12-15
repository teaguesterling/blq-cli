# Shell Completions & Utilities

## completions

Generate shell completion scripts for bash, zsh, or fish.

### Usage

```bash
blq completions <shell>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `shell` | Shell type: `bash`, `zsh`, or `fish` |

### Installation

#### Bash

Add to `~/.bashrc`:

```bash
eval "$(blq completions bash)"
```

Or save to system completions:

```bash
blq completions bash > /etc/bash_completion.d/blq
```

#### Zsh

Add to `~/.zshrc`:

```bash
eval "$(blq completions zsh)"
```

Or save to your fpath:

```bash
blq completions zsh > ~/.zsh/completions/_blq
```

#### Fish

Save to completions directory:

```bash
blq completions fish > ~/.config/fish/completions/blq.fish
```

### Features

The completion scripts provide:

- All subcommands with descriptions
- Registered command names for `blq run`
- Command-specific options (e.g., `--json`, `--limit`)
- File completion for `import`, `query`, `filter`
- Shell type completion for `completions` itself

### Example

After installation, typing `blq <TAB>` shows all available commands:

```
$ blq <TAB>
capture      errors       formats      prune        shell        sync
commands     event        history      query        sql          unregister
completions  exec         import       register     status       warnings
context      filter       init         run          summary
```

Typing `blq run <TAB>` shows registered commands from `.lq/commands.yaml`.

---

## formats

List available log formats supported by the duck_hunt extension.

### Usage

```bash
blq formats
```

### Output

Shows formats grouped by category:

```
Available log formats (via duck_hunt):

  build:
    gcc                      GCC/Clang compiler output
    make                     Make build output
    cmake                    CMake output
    msbuild                  MSBuild output
    ...

  test:
    pytest                   Python pytest output
    jest                     JavaScript Jest output
    mocha                    JavaScript Mocha output
    ...

  lint:
    eslint                   ESLint output
    pylint                   Pylint output
    ...
```

### Notes

- Requires the `duck_hunt` DuckDB extension
- If duck_hunt is not available, shows a message explaining how to install it
- Use format names with `-F` flag: `blq run -F pytest pytest tests/`
