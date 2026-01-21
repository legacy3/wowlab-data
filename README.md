# wowlab-data

Game data tables extracted from WoW client builds.

Data provided by [wago.tools](https://wago.tools/) - thank you!

## Updating Data

To update with a new patch:

```bash
python scripts/update-data.py <zip_url> <version>
```

This will:

1. Download and extract the new CSV data to `data/tables/`
2. Compare headers with the previous version
3. Generate a structure diff at `changes/<version>.md`

## Directory Structure

- `data/tables/` - CSV files for each game table
- `changes/` - Markdown files documenting structure changes between versions
- `scripts/` - Utility scripts
