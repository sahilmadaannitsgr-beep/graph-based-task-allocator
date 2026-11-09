# Shared project data layer

This directory is outside `component_1/` and is shared by every project component.

- `schema/database_schema.sql`: canonical relational database schema.
- `database/seed_database.py`: initialize stable device/task bit mappings and import legacy Component 2 vehicle rows.
- `database/db_utils.py`: SQLite connection, schema initialization, and migration utilities.
- `database/project.db`: generated local database; create it with the seed command.

From the repository root:

```bash
python shared/database/seed_database.py
```

Set `PROJECT_DB_PATH` in components/services to use a non-default database path.
SQLite is used for local development; a shared PostgreSQL deployment is preferable
when components run on separate machines.

Contracts:
- `schema/gps_observation.schema.json`: unchanged Component 1 four-field GPS input.
- `schema/component2_vehicle_state.schema.json`: exact six-field Component 2 output.

To bulk import backend-approved vehicle inventories, copy
`database/vehicle_capabilities.example.json`, add confirmed device names and exact
vehicle IDs, then run:

```bash
python shared/database/import_vehicle_capabilities.py --input shared/database/my_vehicle_capabilities.json
```

Vehicle capabilities are never inferred from GPS observations.
