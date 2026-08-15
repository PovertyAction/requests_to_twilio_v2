/**
 * Appends a Twilio Studio flow's collected answers as a row in a Google Sheet.
 *
 * One of two publish targets, and the one with the lowest barrier to entry: a
 * spreadsheet, a service account, and everybody on the team can open the result
 * and read it without being taught a query language. Its sibling,
 * publish_motherduck.js, occupies the same position in the graph and writes the
 * same payload to a warehouse instead. Pick between them at build time with
 * `just build-demo-flow --publish-target gsheets|motherduck`.
 *
 * Deploy this as a Twilio Function and call it from a Function widget placed at
 * the end of your Studio flow. Every terminal path must route through it, or a
 * break-off produces no row.
 *
 * ---------------------------------------------------------------------------
 * SETUP
 *
 * 1. Add the `googleapis` package as a Dependency on the Function Service.
 *
 * 2. Environment Variables on the Function Service (never in this file: a
 *    Twilio Function's source is not a secret store, and an earlier version of
 *    this file leaked a live service-account key into a public GitHub
 *    repository, where it sat for roughly 19 months).
 *
 *      GOOGLE_CLIENT_EMAIL   Service account address, e.g.
 *                            sheets-writer@<project>.iam.gserviceaccount.com
 *      GOOGLE_PRIVATE_KEY_1  The service account's PEM private key, split
 *      GOOGLE_PRIVATE_KEY_2  across numbered variables - see readPrivateKey
 *      ...                   below for why it cannot be one.
 *      GOOGLE_SHEET_ID       The target spreadsheet's ID, from its URL.
 *
 *    `just deploy-functions` sets all of these from the service-account JSON
 *    named by GOOGLE_SERVICE_ACCOUNT_FILE in .env, including the splitting.
 *
 * 3. Share the sheet with GOOGLE_CLIENT_EMAIL as an Editor. A service account
 *    is a principal in its own right; creating it grants it nothing.
 *
 * 4. Put a header row in the sheet. Columns are discovered from it, exactly as
 *    publish_motherduck discovers them from information_schema: a parameter
 *    whose name matches a header is written to that column, anything else is
 *    dropped. Generate the row from the instrument with
 *    `rtt flow schema <flow.json> --format header` rather than typing it.
 * ---------------------------------------------------------------------------
 */

const SCOPES = ["https://www.googleapis.com/auth/spreadsheets"];

/**
 * Loads the Google client, on first use rather than at module load.
 *
 * Required lazily so that everything in this file which does NOT talk to Google
 * - the key reassembly, the header-to-column mapping, the dropped-parameter
 * check - can be loaded and tested in a bare Node process with no node_modules.
 * Those are the parts most likely to drift away from the Python that feeds them
 * and the parts a test can actually pin. Twilio installs `googleapis` as a
 * Service Dependency, so at runtime this resolves normally.
 *
 * @returns {{google: object, sheets: object}} The Google client and Sheets API.
 */
function googleClient() {
  // eslint-disable-next-line global-require
  const { google } = require("googleapis");
  return { google, sheets: google.sheets("v4") };
}

// Columns A to FP. Widen if your sheet has more than 172 columns - and note
// that this ceiling is real, and is one of the two reasons a round with many
// questions belongs in publish_motherduck instead.
const HEADER_COLUMNS = "A1:FP1";
const APPEND_ANCHOR = "A1";

/**
 * Qualifies a range with the target tab, when one is configured.
 *
 * An unqualified range means *the first visible tab*, not "the tab you were
 * thinking of". That is fine for a workbook with one sheet and quietly wrong
 * the moment a second is added - dragging a new tab to the front, or hiding the
 * old one, silently redirects every submission into it, against whatever header
 * row it happens to have. Rows keep arriving and the Function keeps returning
 * 200, so nothing reports the change.
 *
 * Set GOOGLE_SHEET_TAB as soon as the workbook has more than one tab, which it
 * will the first time anyone adds a monitoring or analysis sheet beside the
 * responses.
 *
 * @param {string|undefined} tab The configured tab name, if any.
 * @param {string} range An A1 range, e.g. "A1:FP1".
 * @returns {string} The range, qualified with the tab when one is set.
 */
function qualify(tab, range) {
  // Single quotes are the escape in A1 notation, and a tab named "Round 1" or
  // "Baseline (EN)" needs them. A literal quote inside the name doubles.
  return tab ? `'${String(tab).replace(/'/g, "''")}'!${range}` : range;
}

// Retry once on a transient Sheets API failure before giving up.
const MAX_ATTEMPTS = 2;

// Studio passes bookkeeping alongside the parameters you configure. Kept
// identical to publish_motherduck.js: these are not answers and must not be
// reported as dropped columns.
const RESERVED_KEYS = new Set(["request", "UserIdentity"]);

/**
 * Reassembles the service account's private key from the environment.
 *
 * Twilio rejects an environment variable value over 450 bytes. A Google service
 * account's PEM key is an RSA 2048 private key of roughly 1,700 bytes, so it
 * cannot be stored in one variable at all - the deploy call fails outright.
 * `deploy_twilio_functions.py` splits it into GOOGLE_PRIVATE_KEY_1, _2, ... and
 * this joins them back in order, the same treatment MOTHERDUCK_TOKEN already
 * needed for the same reason.
 *
 * A single GOOGLE_PRIVATE_KEY is still honoured first, so a key set by hand in
 * the Console keeps working.
 *
 * @param {object} context The Twilio Function context.
 * @returns {string} The complete PEM key, or an empty string if absent.
 */
function readPrivateKey(context) {
  let raw = context.GOOGLE_PRIVATE_KEY;

  if (!raw) {
    const parts = [];
    for (let index = 1; context[`GOOGLE_PRIVATE_KEY_${index}`]; index += 1) {
      parts.push(context[`GOOGLE_PRIVATE_KEY_${index}`]);
    }
    raw = parts.join("");
  }

  // Twilio env vars cannot hold real newlines, so the key travels with literal
  // "\n" two-character sequences. Restore them, or the PEM parser rejects it
  // with an error that says nothing about newlines.
  return raw ? raw.replace(/\\n/g, "\n") : "";
}

/**
 * Reads the required environment variables, failing loudly if any is missing.
 * A missing variable here means the flow silently drops responses, so it is
 * better to throw than to continue.
 * @param {object} context The Twilio Function context.
 * @returns {{clientEmail: string, privateKey: string, sheetId: string}} Config.
 */
function readConfig(context) {
  const privateKey = readPrivateKey(context);

  const missing = ["GOOGLE_CLIENT_EMAIL", "GOOGLE_SHEET_ID"].filter(
    (name) => !context[name],
  );

  if (!privateKey) {
    missing.unshift("GOOGLE_PRIVATE_KEY (or GOOGLE_PRIVATE_KEY_1, _2, ...)");
  }

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(", ")}. ` +
        "Set them on the Twilio Function Service, not in this file.",
    );
  }

  return {
    clientEmail: context.GOOGLE_CLIENT_EMAIL,
    privateKey,
    sheetId: context.GOOGLE_SHEET_ID,
    // Optional, and unset means the first visible tab - which is the historical
    // behaviour and correct for a single-tab workbook. Set it as soon as there
    // is a second tab. See qualify().
    tab: context.GOOGLE_SHEET_TAB,
  };
}

/**
 * Pauses for `base` milliseconds plus up to 1s of jitter, so that concurrent
 * flow executions retrying at once do not stampede the Sheets API.
 * @param {number} base Base delay in milliseconds.
 * @returns {Promise<void>} Resolves once the delay has elapsed.
 */
function wait(base) {
  const jitter = Math.random() * 1000;
  return new Promise((resolve) => setTimeout(resolve, base + jitter));
}

/**
 * Runs an async operation, retrying once after a short delay.
 * @param {Function} operation Zero-argument function returning a Promise.
 * @param {number} backoffMs Delay before the retry.
 * @returns {Promise<*>} Whatever the operation resolves with.
 */
async function withRetry(operation, backoffMs) {
  let lastError;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt < MAX_ATTEMPTS) {
        await wait(backoffMs);
      }
    }
  }
  throw lastError;
}

/**
 * Lines the event's values up with the sheet's header row, so that answers land
 * in the right columns regardless of the order Studio passes them in.
 *
 * An unanswered question writes an empty cell rather than a placeholder string.
 * The previous version wrote "No Data", which every analysis then had to know
 * about and strip - and which is indistinguishable from a respondent who typed
 * those words. A blank cell is blank in every tool that opens the sheet.
 *
 * @param {string[]} headers The sheet's header values.
 * @param {object} eventData The Function widget's parameters.
 * @returns {{values: Array<Array<string>>, used: string[]}} An append-ready
 *   resource, plus the header names that received a value.
 */
function buildRow(headers, eventData) {
  const used = [];
  const values = headers.map((header) => {
    if (RESERVED_KEYS.has(header)) {
      return "";
    }
    const value = eventData[header];
    if (value === undefined || value === "") {
      return "";
    }
    used.push(header);
    return String(value);
  });
  return { values: [values], used };
}

/**
 * Names the parameters the sheet has nowhere to put.
 *
 * The mirror of the same check in publish_motherduck.js, and the reason both
 * exist: a question added to the flow with no matching header is dropped behind
 * a 200 and a row that looks complete. That is the exact failure this pipeline
 * is built to prevent, so it must be loud even though it cannot be fatal.
 *
 * @param {string[]} headers The sheet's header values.
 * @param {object} eventData The Function widget's parameters.
 * @returns {string[]} Parameter names with no column.
 */
function droppedParameters(headers, eventData) {
  const storable = new Set(headers);
  return Object.keys(eventData).filter(
    (key) => !RESERVED_KEYS.has(key) && !storable.has(key),
  );
}

/**
 * Fetches the sheet's header row.
 * @param {string} accessToken A Google OAuth access token.
 * @param {string} sheetId The target spreadsheet ID.
 * @returns {Promise<string[]>} The header values.
 */
async function getHeaders(sheets, accessToken, sheetId, tab) {
  const range = qualify(tab, HEADER_COLUMNS);
  const result = await withRetry(
    () =>
      sheets.spreadsheets.values.get({
        access_token: accessToken,
        spreadsheetId: sheetId,
        range: [range],
      }),
    3000,
  );

  const headers = result?.data?.values?.[0];
  if (!headers || headers.length === 0) {
    throw new Error(
      `Sheet ${sheetId} has no header row in ${range}; cannot map ` +
        "answers to columns. Generate one with `rtt flow schema <flow.json> " +
        "--format header` and paste it into row 1.",
    );
  }
  return headers;
}

/**
 * Appends one row of answers to the sheet.
 * @param {string} accessToken A Google OAuth access token.
 * @param {string} sheetId The target spreadsheet ID.
 * @param {object} row The append-ready resource from buildRow.
 * @returns {Promise<*>} The Sheets API response.
 */
function appendRow(sheets, accessToken, sheetId, tab, row) {
  return withRetry(
    () =>
      sheets.spreadsheets.values.append({
        access_token: accessToken,
        spreadsheetId: sheetId,
        range: [qualify(tab, APPEND_ANCHOR)],
        resource: { values: row.values },
        valueInputOption: "RAW",
        insertDataOption: "INSERT_ROWS",
      }),
    3500,
  );
}

exports.handler = async function (context, event, callback) {
  try {
    const config = readConfig(context);
    const { google, sheets } = googleClient();

    // Do NOT log `event`: it carries respondent answers, and anything logged
    // here is readable in the Twilio Console by everyone with account access.
    console.log("publish_gsheets: appending one row");

    const jwtClient = new google.auth.JWT(
      config.clientEmail,
      null,
      config.privateKey,
      SCOPES,
    );

    const tokens = await jwtClient.authorize();
    if (!tokens || !tokens.access_token) {
      throw new Error(
        "Service account did not return an access token; check that it has " +
          "access to the sheet.",
      );
    }

    // Stamp the row's arrival time, server-side. Named `submitted_at` to match
    // publish_motherduck and everything downstream of it - `rtt fetch`,
    // `rtt data-check` and the `final_status` rollup all read that column. The
    // previous version called it `date`, which no other part of this toolchain
    // knows about, so the timestamp landed nowhere on a schema-generated sheet.
    event.submitted_at = new Date().toISOString();

    const headers = await getHeaders(
      sheets,
      tokens.access_token,
      config.sheetId,
      config.tab,
    );
    const row = buildRow(headers, event);
    await appendRow(sheets, tokens.access_token, config.sheetId, config.tab, row);

    // Names only: the values are respondent answers, and Console logs are
    // readable by anyone with account access.
    const dropped = droppedParameters(headers, event);
    if (dropped.length > 0) {
      console.error(
        `publish_gsheets: ${dropped.length} parameter(s) had no column in the ` +
          `sheet and were NOT stored: ${dropped.join(", ")}. Regenerate the ` +
          "header row with `rtt flow schema <flow.json> --format header`.",
      );
    }

    console.log(
      `publish_gsheets: wrote ${row.used.length} column(s)` +
        (dropped.length > 0 ? `, dropped ${dropped.length}` : ""),
    );

    return callback();
  } catch (error) {
    // Surface the failure to Studio so the flow's error path can record it. A
    // silently dropped row is the failure mode this whole pipeline exists to
    // avoid.
    console.error(`publish_gsheets failed: ${error.message}`);
    return callback(error);
  }
};

// Exported for tests; unused by Twilio at runtime.
module.exports.buildRow = buildRow;
module.exports.droppedParameters = droppedParameters;
module.exports.readPrivateKey = readPrivateKey;
module.exports.readConfig = readConfig;
module.exports.qualify = qualify;
