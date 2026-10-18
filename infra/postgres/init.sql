-- Two roles per service (#36, docs/plan-deviations.md D1). <svc>_migrator owns the schema and runs
-- Alembic. <svc> is the runtime role and owns nothing: an owner can always drop its tables or grant
-- itself back a revoked right.

\set ON_ERROR_STOP on

\getenv recording_password RECORDING_DB_PASSWORD
\getenv recording_migrator_password RECORDING_MIGRATOR_DB_PASSWORD
\getenv segmenter_password SEGMENTER_DB_PASSWORD
\getenv segmenter_migrator_password SEGMENTER_MIGRATOR_DB_PASSWORD
\getenv notifier_password NOTIFIER_DB_PASSWORD
\getenv notifier_migrator_password NOTIFIER_MIGRATOR_DB_PASSWORD

REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE ROLE recording_migrator LOGIN PASSWORD :'recording_migrator_password';
CREATE ROLE recording LOGIN PASSWORD :'recording_password';
CREATE ROLE segmenter_migrator LOGIN PASSWORD :'segmenter_migrator_password';
CREATE ROLE segmenter LOGIN PASSWORD :'segmenter_password';
CREATE ROLE notifier_migrator LOGIN PASSWORD :'notifier_migrator_password';
CREATE ROLE notifier LOGIN PASSWORD :'notifier_password';

GRANT CONNECT ON DATABASE :"DBNAME"
  TO recording_migrator, recording, segmenter_migrator, segmenter, notifier_migrator, notifier;

CREATE SCHEMA recording AUTHORIZATION recording_migrator;
CREATE SCHEMA segmenter AUTHORIZATION segmenter_migrator;
CREATE SCHEMA notifier AUTHORIZATION notifier_migrator;

GRANT USAGE ON SCHEMA recording TO recording;
GRANT USAGE ON SCHEMA segmenter TO segmenter;
GRANT USAGE ON SCHEMA notifier TO notifier;

ALTER ROLE recording_migrator SET search_path = recording;
ALTER ROLE recording SET search_path = recording;
ALTER ROLE segmenter_migrator SET search_path = segmenter;
ALTER ROLE segmenter SET search_path = segmenter;
ALTER ROLE notifier_migrator SET search_path = notifier;
ALTER ROLE notifier SET search_path = notifier;
