/**
 * Appends a Twilio Studio flow's collected answers as a row in MotherDuck.
 *
 * A drop-in replacement for publish_gsheets.js. It keeps the property that
 * matters - one row appended per submission, the moment the flow reaches this
 * widget - while removing the Google service account, the Sheets API quota, and
 * the 172-column ceiling of a header-row lookup.
 *
 * Deploy as a Twilio Function and call it from a Function widget at the end of
 * your Studio flow, in the same position publish_gsheets occupied. Every
 * terminal path must still route through it, or a break-off produces no row.
 *
 * ---------------------------------------------------------------------------
 * SETUP
 *
 * 1. Add `pg` under the Function Service's Dependencies. MotherDuck speaks the
 *    Postgres wire protocol, so node-postgres works unmodified - it is pure
 *    JavaScript, with no native addon or WASM that a Twilio Function could not
 *    load.
 *
 * 2. Environment Variables on the Function Service (never in this file):
 *
 *      MOTHERDUCK_TOKEN     access token; this is a write credential
 *      MOTHERDUCK_HOST      e.g. pg.us-east-1-aws.motherduck.com
 *      MOTHERDUCK_DATABASE  target database
 *      MOTHERDUCK_TABLE     target table, optionally schema-qualified
 *
 * 3. Create the destination table. This function will not create one: guessing
 *    a schema for survey data is how columns end up as the wrong type, and a
 *    silent CREATE would mask a typo in MOTHERDUCK_TABLE. If the table is
 *    missing it fails with the exact DDL to run.
 *
 * ---------------------------------------------------------------------------
 * Columns are discovered from the table itself, the same way publish_gsheets
 * read the sheet's header row: parameters whose names match a column are
 * inserted, anything else is ignored. Adding a question therefore means adding
 * a column, and the flow starts filling it with no change here.
 * ---------------------------------------------------------------------------
 */

const { Client } = require("pg");

// Twilio Functions time out at 10 seconds, so fail fast enough to log a real
// error rather than being killed mid-connection.
const CONNECT_TIMEOUT_MS = 4000;
const QUERY_TIMEOUT_MS = 4000;

// Studio passes bookkeeping alongside the parameters you configure.
const RESERVED_KEYS = new Set(["request", "UserIdentity"]);

/**
 * Reads and validates configuration from the environment.
 * @param {object} context The Twilio Function context.
 * @returns {{token: string, host: string, database: string, table: string}} Config.
 */
function readConfig(context) {
  const missing = [
    "MOTHERDUCK_TOKEN",
    "MOTHERDUCK_HOST",
    "MOTHERDUCK_DATABASE",
    "MOTHERDUCK_TABLE",
  ].filter((name) => !context[name]);

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(", ")}. ` +
        "Set them on the Twilio Function Service, not in this file.",
    );
  }

  return {
    token: context.MOTHERDUCK_TOKEN,
    host: context.MOTHERDUCK_HOST,
    database: context.MOTHERDUCK_DATABASE,
    table: context.MOTHERDUCK_TABLE,
  };
}

/**
 * Splits a table name into schema and table.
 *
 * Accepts all three forms MotherDuck allows. The three-part case is the trap:
 * in `database.schema.table` the *middle* segment is the schema, so treating
 * the first as the schema looks up a table that does not exist and reports the
 * table as missing when it is right there.
 *
 * @param {string} table "table", "schema.table", or "database.schema.table".
 * @returns {{schema: string, name: string}} The schema and table name.
 */
function splitTable(table) {
  const parts = table.split(".");
  if (parts.length >= 3) {
    return { schema: parts[parts.length - 2], name: parts[parts.length - 1] };
  }
  if (parts.length === 2) {
    return { schema: parts[0], name: parts[1] };
  }
  return { schema: "main", name: table };
}

/**
 * Reads the destination table's column names.
 *
 * This mirrors how publish_gsheets read the sheet's header row: the table
 * defines the shape, and the flow fills whatever it recognises.
 *
 * @param {import("pg").Client} client A connected client.
 * @param {string} table The configured table name.
 * @returns {Promise<string[]>} Column names.
 */
async function getColumns(client, table) {
  const { schema, name } = splitTable(table);

  const result = await client.query(
    `SELECT column_name
       FROM information_schema.columns
      WHERE table_schema = $1 AND table_name = $2`,
    [schema, name],
  );

  if (result.rows.length === 0) {
    throw new Error(
      `Table ${table} does not exist or has no columns. Create it first, ` +
        `for example:\n  CREATE TABLE ${table} (caseid VARCHAR, ` +
        `set_complete VARCHAR, submitted_at TIMESTAMP);\n` +
        "Survey values are inserted as text; cast in your analysis queries.",
    );
  }

  return result.rows.map((row) => row.column_name);
}

/**
 * Builds a parameterised INSERT for the event values that match real columns.
 *
 * Values are always bound as parameters, never interpolated: respondent answers
 * are free text arriving from outside, and string-building a query with them
 * would be an injection route straight into the warehouse.
 *
 * @param {string} table The destination table.
 * @param {string[]} columns The table's columns.
 * @param {object} event The Function widget's parameters.
 * @returns {{text: string, values: Array, used: string[]}|null} The query, or null if nothing matched.
 */
function buildInsert(table, columns, event) {
  const used = [];
  const values = [];

  for (const column of columns) {
    if (!(column in event) || RESERVED_KEYS.has(column)) {
      continue;
    }
    const value = event[column];
    used.push(column);
    // Unanswered questions arrive undefined; store NULL rather than the string
    // "undefined", so a genuine blank stays distinguishable from an answer.
    values.push(value === undefined || value === "" ? null : String(value));
  }

  // Stamp arrival time if the table has somewhere to put it.
  if (columns.includes("submitted_at") && !used.includes("submitted_at")) {
    used.push("submitted_at");
    values.push(new Date().toISOString());
  }

  if (used.length === 0) {
    return null;
  }

  const quoted = used.map((c) => `"${c.replace(/"/g, '""')}"`).join(", ");
  const placeholders = used.map((_, i) => `$${i + 1}`).join(", ");

  return {
    text: `INSERT INTO ${table} (${quoted}) VALUES (${placeholders})`,
    values,
    used,
  };
}

exports.handler = async function (context, event, callback) {
  let client;
  try {
    const config = readConfig(context);

    // Do NOT log `event`: it carries respondent answers, and Twilio Console
    // logs are readable by everyone with account access.
    console.log("publish_motherduck: appending one row");

    client = new Client({
      host: config.host,
      port: 5432,
      user: "postgres",
      password: config.token,
      database: config.database,
      ssl: { rejectUnauthorized: true },
      connectionTimeoutMillis: CONNECT_TIMEOUT_MS,
      query_timeout: QUERY_TIMEOUT_MS,
    });

    await client.connect();

    const columns = await getColumns(client, config.table);
    const insert = buildInsert(config.table, columns, event);

    if (insert === null) {
      throw new Error(
        `None of the widget's parameters match a column in ${config.table}. ` +
          `Table columns: ${columns.join(", ")}`,
      );
    }

    await client.query(insert.text, insert.values);
    console.log(`publish_motherduck: wrote ${insert.used.length} column(s)`);

    return callback();
  } catch (error) {
    // Surface the failure to Studio so the flow's error path can record it.
    // A silently dropped row is the failure mode this whole pipeline is built
    // to avoid.
    console.error(`publish_motherduck failed: ${error.message}`);
    return callback(error);
  } finally {
    if (client) {
      await client.end().catch(() => {});
    }
  }
};

// Exported for tests; unused by Twilio at runtime.
module.exports.buildInsert = buildInsert;
module.exports.splitTable = splitTable;
module.exports.readConfig = readConfig;
