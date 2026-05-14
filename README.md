# wowlab-data

Game data tables extracted from WoW client builds.

Data provided by [wago.tools](https://wago.tools/) - thank you!

## Updating Data

### Automated Wago update

```bash
pnpm install
pnpm refresh
```

For the default full update, run:

```bash
./run.sh
```

`run.sh` prompts for `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` when assets are enabled. You can also export them ahead of time for unattended runs.

This is the main update entrypoint. It resolves the latest retail WoW build from `https://wago.tools/api/builds`, downloads every currently tracked DB2 table from `https://wago.tools/db2/<TableName>/csv?version=<build>`, replaces `data/tables/`, and writes:

- `changes/<version>.md` - structure diff from the previous checked-in tables
- `changes/metadata/<version>.json` - Wago source/build metadata and failures

Useful options:

```bash
pnpm refresh:dry-run
pnpm refresh -- --product wowxptr
pnpm refresh -- --version 12.0.5.67451
pnpm refresh -- --skip-assets
pnpm refresh -- --skip-tables
```

By default it also refreshes Wago CASC-derived PNG assets from the same resolved build and uploads them to the `encounter-images` Supabase Storage bucket:

```bash
pnpm refresh
```

Asset paths in that bucket are:

- `journal/images/` for Journal instance background/button/lore art
- `journal/manifest.json` and `journal/journal-instance-images.json`
- `loadscreens/images/` for full loadscreen images
- `loadscreens/seo/` for loadscreen SEO crops
- `loadscreens/manifest.json`

Use `--skip-assets` when you only want the CSV tables. Use `--skip-tables` when you only want to seed Supabase image assets from the current checked-in CSV tables.

## Directory Structure

- `data/tables/` - CSV files for each game table
- `changes/` - Markdown files documenting structure changes between versions
- `scripts/` - Utility scripts
