# Frozen legacy activation sources

These gzip files are exact `git show 27aadf8:runner/{manager,host-helper}/...` source bytes from before the opt-in parallel pool. The loader validates their original SHA-256 pins. Historical activation tools keep their existing pins and negative/tamper/rollback tests; they must not implicitly approve the new manager/helper. These fixtures are test data, never installation inputs. Decompress with `gzip -dc manager.py.gz` or `gzip -dc helper.py.gz` to inspect.
