# Contributing to Manon

## Welcome

Thanks for your interest in contributing to Manon! This guide will help you get started.

## Development Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/manon.git
cd manon

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r manon_mcp/requirements.txt
pip install -r saas/requirements.txt

# Install in development mode
pip install -e .
```

## Project Structure

```
manon_mcp/  - MCP server (IDE integration)
saas/       - Backend API server
core/       - Core utilities
web/        - Web interface (optional)
docs/       - Documentation
```

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feat/your-feature-name
# or
git checkout -b fix/bug-description
```

### 2. Code Style

- Follow PEP 8 for Python code
- Use type hints where possible
- Add docstrings for public functions
- Keep functions focused and small

### 3. Testing

```bash
# Test MCP tools
python run_mcp.py

# Test SaaS backend
python -m saas.main
```

### 4. Commit Messages

Use clear, descriptive commit messages:

```
feat: add TypeScript import resolution
fix: correct language detection for .mjs files
docs: update deployment guide
refactor: simplify AST sync logic
```

## Pull Request Process

1. **Update documentation** if you changed APIs or added features
2. **Test your changes** in a clean environment
3. **Update CHANGELOG.md** with your changes
4. **Submit PR** with clear description of what and why

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactoring

## Testing
How did you test this?

## Checklist
- [ ] Code follows project style
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
```

## Areas for Contribution

### High Priority

- **Language support**: Add more tree-sitter parsers
- **Documentation**: Improve guides and examples
- **Testing**: Add unit and integration tests
- **Performance**: Optimize graph operations

### Good First Issues

- Fix typos in documentation
- Add examples to README
- Improve error messages
- Add configuration validation

## Code Review

All submissions require review. We aim to:

- Respond within 48 hours
- Provide constructive feedback
- Merge quickly when ready

## Community

- **Issues**: Report bugs or request features
- **Discussions**: Ask questions or share ideas
- **Discord**: (Coming soon)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
