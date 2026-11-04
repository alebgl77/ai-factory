-- SPDX-License-Identifier: MIT
-- First-start initialisation of the shared PostgreSQL (official postgres:17 image).
-- Run once by docker-entrypoint-initdb.d when the data volume is empty.
-- One database and one least-privilege owner role per service; the passwords come from
-- the environment of the postgres container (LITELLM_DB_PASSWORD, LANGFUSE_DB_PASSWORD).

\set ON_ERROR_STOP on
\getenv litellm_password LITELLM_DB_PASSWORD
\getenv langfuse_password LANGFUSE_DB_PASSWORD

CREATE ROLE litellm LOGIN PASSWORD :'litellm_password';
CREATE ROLE langfuse LOGIN PASSWORD :'langfuse_password';

CREATE DATABASE litellm OWNER litellm;
CREATE DATABASE langfuse OWNER langfuse;

REVOKE ALL ON DATABASE litellm FROM PUBLIC;
REVOKE ALL ON DATABASE langfuse FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE litellm TO litellm;
GRANT CONNECT, TEMPORARY ON DATABASE langfuse TO langfuse;
