# wowlab-data

Game data tables extracted from WoW client builds.

Data provided by [wago.tools](https://wago.tools/) - thank you!

## Updating Data

### Automated Wago update

```bash
pnpm install
pnpm refresh
```

For the default full update, you can also run:

```bash
./run.sh
```

This is the main update entrypoint. It resolves the latest retail WoW build from `https://wago.tools/api/builds`, downloads every currently tracked DB2 table from `https://wago.tools/db2/<TableName>/csv?version=<build>`, replaces `data/tables/`, and writes:

- `changes/<version>.md` - structure diff from the previous checked-in tables
- `changes/metadata/<version>.json` - Wago source/build metadata and failures

Useful options:

```bash
pnpm refresh:dry-run
pnpm refresh -- --product wowxptr
pnpm refresh -- --version 12.0.5.67451
pnpm refresh -- --skip-assets
```

By default it also refreshes Wago CASC-derived PNG assets from the same resolved build:

```bash
pnpm refresh
```

Asset outputs default to:

- `data/images/journal/` for Journal instance background/button/lore art
- `data/images/loadscreens/` for loadscreen images, SEO crops, and manifests

Use `--skip-assets` when you only want the CSV tables.

## Directory Structure

- `data/tables/` - CSV files for each game table
- `data/images/` - generated PNG assets and manifests derived from Wago CASC files
- `changes/` - Markdown files documenting structure changes between versions
- `scripts/` - Utility scripts
