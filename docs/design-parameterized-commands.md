# Design: Parameterized Commands

**Status:** Implemented
**Date:** 2026-02-10

## Overview

Extend the command registry to support parameterized commands with template substitution.

## Command Types

### Simple Command (`cmd`)

No substitution. Runs as-is.

```toml
[commands.lint]
cmd = "ruff check ."
description = "Run linter"
```

### Parameterized Command (`tpl`)

Template with `{param}` placeholders. Requires substitution before execution.

```toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v" }
description = "Run tests"
```

## Syntax

### Template Placeholders

Use `{param}` syntax (Python str.format style):

```toml
tpl = "pytest {path} {flags}"
tpl = "make -j{jobs} {target}"
tpl = "docker build -t {image}:{tag} ."
```

**Escaping:** Use `{{` and `}}` for literal braces:
```toml
tpl = "echo '{{not a param}}' && run {actual_param}"
```

### Defaults

Inline table for simple cases:

```toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v --tb=short" }
```

### Required Parameters

Parameters without defaults are required:

```toml
[commands.test-file]
tpl = "pytest {file} -v"
# No defaults = file is required

[commands.build]
tpl = "make -j{jobs} {target}"
defaults = { jobs = "4" }
# jobs has default, target is required
```

### Complex Parameter Definitions

For descriptions, types, or validation - use nested params section:

```toml
[commands.deploy]
tpl = "kubectl apply -f {manifest} --namespace {ns}"
defaults = { ns = "default" }

[commands.deploy.params]
manifest = { description = "Path to manifest file", type = "path" }
ns = { description = "Kubernetes namespace" }
```

## CLI Usage

### Named Parameters

```bash
blq run test --path tests/unit/ --flags "-vvs"
blq run test-file --file src/main.py
```

### Key=Value Style

```bash
blq run test path=tests/unit/ flags=-vvs
blq run build target=clean jobs=8
```

### Mixed (named + extra args)

```bash
blq run test --path tests/unit/ -- --capture=no
# Becomes: pytest tests/unit/ -v --tb=short --capture=no
```

### Help

```bash
blq run test --help
# Shows: available parameters, defaults, descriptions
```

## Parameter Resolution

Order of precedence (highest to lowest):
1. CLI arguments (`--param value` or `param=value`)
2. Environment variables (`BLQ_PARAM_<NAME>`)
3. Defaults from `commands.toml`
4. Error if required and not provided

## Implementation

### RegisteredCommand Changes

```python
@dataclass
class RegisteredCommand:
    name: str
    cmd: str | None = None           # Simple command
    tpl: str | None = None           # Template command
    defaults: dict[str, str] = field(default_factory=dict)
    params: dict[str, ParamDef] = field(default_factory=dict)
    description: str = ""
    timeout: int = 300
    format_hint: str | None = None
    capture: bool = True

    @property
    def is_template(self) -> bool:
        return self.tpl is not None

    def render(self, args: dict[str, str]) -> str:
        """Render template with provided args + defaults."""
        if not self.is_template:
            return self.cmd

        # Merge defaults with provided args
        merged = {**self.defaults, **args}

        # Check for missing required params
        required = self.required_params()
        missing = required - set(merged.keys())
        if missing:
            raise ValueError(f"Missing required params: {missing}")

        return self.tpl.format(**merged)

    def required_params(self) -> set[str]:
        """Parameters without defaults."""
        all_params = set(re.findall(r'\{(\w+)\}', self.tpl))
        return all_params - set(self.defaults.keys())

@dataclass
class ParamDef:
    description: str = ""
    type: str = "string"  # string, path, int, choice
    choices: list[str] | None = None
```

### CLI Changes

```python
# In run command handler
if cmd.is_template:
    # Parse param args from remaining argv
    params = parse_param_args(remaining_args, cmd)
    rendered_cmd = cmd.render(params)
else:
    rendered_cmd = cmd.cmd
```

### MCP Changes

```python
@mcp.tool()
def run(
    command: str,
    args: dict[str, str] | None = None,  # NEW: template params
    extra: list[str] | None = None,
    timeout: int = 300,
) -> RunResult:
    ...
```

## Examples

### Basic

```toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v" }
```

```bash
blq run test                        # pytest tests/ -v
blq run test path=tests/unit/       # pytest tests/unit/ -v
blq run test --flags "-vvs -x"      # pytest tests/ -vvs -x
```

### Required Parameter

```toml
[commands.test-file]
tpl = "pytest {file} -v --tb=short"
```

```bash
blq run test-file                   # Error: missing required param 'file'
blq run test-file file=test_foo.py  # pytest test_foo.py -v --tb=short
```

### Multiple with Descriptions

```toml
[commands.docker-build]
tpl = "docker build -t {image}:{tag} {context}"
defaults = { tag = "latest", context = "." }

[commands.docker-build.params]
image = { description = "Image name (required)" }
tag = { description = "Image tag" }
context = { description = "Build context path" }
```

```bash
blq run docker-build --help
# Parameters:
#   image   - Image name (required)
#   tag     - Image tag (default: latest)
#   context - Build context path (default: .)

blq run docker-build image=myapp
# docker build -t myapp:latest .

blq run docker-build image=myapp tag=v1.2.3
# docker build -t myapp:v1.2.3 .
```

## Migration

Existing `cmd` fields continue to work unchanged. `tpl` is additive.

## Future Extensions

- **Type validation:** `type = "int"` for numeric params
- **Choices:** `choices = ["dev", "staging", "prod"]`
- **Environment interpolation:** `tpl = "... --token ${API_TOKEN}"`
- **Conditional sections:** `tpl = "cmd {?verbose:--verbose}"`
