# Release Conventions & Packaging Research

**Date**: 2026-09-12
**Scope**: Externally relevant release conventions and GitHub Actions/packaging expectations for AI-Usage-Tray

---

## 1. Detected Technologies

| Technology | Version/Config | Role |
|---|---|---|
| Python | 3.13 | Primary language |
| PyInstaller | (latest) | Bundles app into single .exe |
| GitHub Actions | workflows/build.yml | CI/CD pipeline |
| softprops/action-gh-release | v3 | Creates GitHub Releases |
| PyQt6 | >=6.5.0 | UI framework |
| pystray | >=0.19.5 | System tray integration |

**Current tag state**: v1.0.0 through v1.0.8 (latest)
**Next patch release**: v1.0.9

---

## 2. Python Versioning (PEP 440)

### Reference
- **Source**: [PEP 440 - Version Identification and Dependency Specification](https://peps.python.org/pep-0440/)
- **Spec**: [Python Packaging - Version Specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/)

### Key Conventions

1. **Semantic Versioning compatibility**: PEP 440 fully supports `Major.Minor.Patch` (described as `major.minor.micro` in the PEP). Abiding by these aspects is encouraged.

2. **Patch release definition**: A patch release (e.g., `1.0.6` to `1.0.7`) is for backward-compatible bug fixes only. No new features, no breaking changes.

3. **Version format**: `X.Y.Z` where:
   - `X` = major (breaking changes)
   - `Y` = minor (new features, backward compatible)
   - `Z` = patch/micro (bug fixes, backward compatible)

4. **Tag format**: The project already uses `v1.0.8` format, which is standard and correct. The `v` prefix is conventional for GitHub releases.

### Recommendation for Patch Release

```
v1.0.8 -> v1.0.9
```

- Increment only the patch number
- No pre-release tags needed (`.dev`, `.rc`, etc.) for stable patches
- Ensure the commit message clearly describes the fix

---

## 3. GitHub Actions Release Workflow

### Reference
- **Source**: [Softprops/action-gh-release](https://github.com/softprops/action-gh-release)
- **Best Practices**: [Secure Binary Releases for GitHub Actions](https://binaries.live/how-to-host-binary-releases-securely-for-github-actions)
- **Tag-based strategies**: [Modern Release Strategies with GitHub Actions](https://www.mostlylucid.net/blog/release-strategies-github-actions)

### Current Workflow Analysis

The existing `build.yml` workflow triggers on `v*` tag push. This is a **tag-based deployment** strategy, which is the recommended approach for solo developers, small teams, and desktop applications.

### What the Current Workflow Does

1. Triggers on `v*` tag push
2. Builds on `windows-latest`
3. Runs tests with pytest
4. Builds .exe with PyInstaller
5. Uploads artifact
6. Creates GitHub Release with `softprops/action-gh-release@v2`

### Recommendations for Patch Release Workflow

#### A. Tag Naming Convention

Follow the established pattern:
```bash
    git tag v1.0.9
    git push origin v1.0.9
```

#### B. Release Notes

The workflow uses `generate_release_notes: true`, which auto-generates notes from commits since the last tag. For patch releases, ensure:

- Commit messages follow Conventional Commits (already using `fix:`, `feat:`)
- Patch-specific fixes are clearly documented

#### C. Artifact Naming

Current naming: `AIUsageTray.exe`

**Recommendation**: Include version in artifact name for clarity:
```yaml
- name: Upload artifact
  uses: actions/upload-artifact@v4
  with:
    name: AIUsageTray-${{ github.ref_name }}
    path: python/dist/AIUsageTray.exe
```

#### D. Checksum Generation

**Best Practice**: Generate SHA-256 checksums for release assets. This is increasingly expected for binary releases per supply chain security guidelines.

---

## 4. PyInstaller Build Conventions

### Reference
- **Source**: [PyInstaller Documentation](https://pyinstaller.org/)

### Current Build Configuration

The workflow uses a comprehensive PyInstaller command with:
- `--onefile` (single .exe)
- `--windowed` (no console)
- `--icon icon.ico`
- Multiple `--hidden-import` directives
- `--collect-submodules providers`
- `--collect-all PyQt6`

### Recommendations

1. **Version embedding**: Consider embedding version info in the .exe using `pyinstaller-versionfile` or a `.spec` file with `version` field. This allows Windows to show version in file properties.

2. **Clean builds**: The `--noconfirm --clean` flags are correctly used. For patch releases, this ensures reproducibility.

3. **Python version pinning**: The workflow uses `python-version: '3.13'`. For consistency, this is fine, but be aware that PyInstaller may have version-specific quirks.

4. **Dependency pinning**: `requirements.txt` uses `>=` constraints:
   ```
   pystray>=0.19.5
   Pillow>=10.0.0
   requests>=2.31.0
   certifi>=2023.7.22
   PyQt6>=6.5.0
   ```

   **Recommendation**: For reproducible builds, consider pinning exact versions to ensure the same .exe is produced regardless of when the build runs.

---

## 5. GitHub Release Best Practices

### Release Integrity Checklist

- [x] Every release maps to a specific commit/tag
- [x] Release assets are uploaded via GitHub Actions
- [ ] Checksums are generated (recommended)
- [ ] Version is embedded in .exe properties (optional)
- [ ] Release notes identify known issues (recommended)

### Naming Convention

Current: `AIUsageTray.exe`

Recommended for clarity: `AIUsageTray-v1.0.7.exe`

### Release Description

For patch releases, the release description should include:

1. **Version**: v1.0.9
2. **Changes**: Bullet list of fixes
3. **Known Issues**: Any limitations
4. **Verification**: How to verify the .exe (checksum)

---

## 6. Patch Release Checklist

### Pre-Release

1. Ensure all fixes are committed and tested
2. Run tests locally: `cd python && python -m pytest tests -q`
3. Verify no breaking changes (API compatibility, config format)

### Tag & Release

1. Create tag:
   ```bash
   git tag v1.0.7
   git push origin v1.0.7
   ```

2. Monitor GitHub Actions workflow
3. Verify .exe is uploaded as release asset
4. Review auto-generated release notes
5. Edit release description if needed (add fix summary)

### Post-Release

1. Verify download works from GitHub Releases page
2. Test .exe on clean Windows environment (if possible)
3. Update any external documentation referencing version

---

## 7. Future Enhancements (Optional)

### Supply Chain Security

Consider adding artifact attestation for stronger provenance. This achieves SLSA Level 2 and allows users to verify the binary was built in GitHub Actions.

### Multi-Architecture Builds

Currently builds only for Windows. If cross-platform support is needed:
- Consider matrix builds (windows-latest, ubuntu-latest, macos-latest)
- Use conditional steps for platform-specific packaging

### Automated Version Bumping

Consider tools like `bump2version` or `commitizen` to automate version increments and changelog generation.

---

## Sources

1. PEP 440: https://peps.python.org/pep-0440/
2. Python Packaging Version Specifiers: https://packaging.python.org/en/latest/specifications/version-specifiers/
3. Softprops/action-gh-release: https://github.com/softprops/action-gh-release
4. Secure Binary Releases for GitHub Actions: https://binaries.live/how-to-host-binary-releases-securely-for-github-actions
5. Modern Release Strategies: https://www.mostlylucid.net/blog/release-strategies-github-actions
6. GitHub Artifact Attestations: https://github.blog/changelog/2024-06-25-artifact-attestations-is-generally-available/
7. PyInstaller Documentation: https://pyinstaller.org/
8. Conventional Commits: https://www.conventionalcommits.org/
