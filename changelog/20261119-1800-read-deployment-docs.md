major

The galtea skill reads every platform fact from the docs of the platform the user is logged in to, through `galtea docs`, and checks the known issues of that release before it builds anything. The skill keeps only how to work: install, log in, read the docs, and act. When the deployment's docs cannot be read, it uses the public docs and says they describe the newest release. A CLI that exits with code 10 or has no `galtea docs` is updated first.
