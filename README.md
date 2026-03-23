# wowlab-data

Game data tables extracted from WoW client builds.

Data provided by [wago.tools](https://wago.tools/) - thank you!

## Updating Data

```bash
python scripts/update-data.py <source> <version>
```

The source can be:

- A **URL** to a zip file: `python scripts/update-data.py https://example.com/data.zip 12.0.5.66529`
- A **local archive** (.zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz): `python scripts/update-data.py /path/to/data.zip 12.0.5.66529`
- A **local directory** of CSV files: `python scripts/update-data.py /path/to/csvs/ 12.0.5.66529`

This will:

1. Replace the CSV data in `data/tables/` with the new source
2. Compare headers with the previous version
3. Generate a structure diff at `changes/<version>.md`

## Directory Structure

- `data/tables/` - CSV files for each game table
- `changes/` - Markdown files documenting structure changes between versions
- `scripts/` - Utility scripts
