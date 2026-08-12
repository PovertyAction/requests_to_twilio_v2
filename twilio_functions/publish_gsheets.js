/**
 * Appends a Twilio Studio flow's collected answers as a row in a Google Sheet.
 *
 * Deploy this as a Twilio Function and call it from a Function widget placed at
 * the end of your Studio flow.
 *
 * ---------------------------------------------------------------------------
 * REQUIRED ENVIRONMENT VARIABLES (Twilio Console > Functions > Service >
 * Environment Variables). Never hard-code these into this file: a Twilio
 * Function's source is not a secret store, and an earlier version of this file
 * leaked a live service-account key into a public GitHub repository.
 *
 *   GOOGLE_CLIENT_EMAIL   Service account address, e.g. sheets-writer@<proj>.iam.gserviceaccount.com
 *   GOOGLE_PRIVATE_KEY    The service account's PEM private key. Twilio env vars
 *                         cannot hold real newlines, so paste it with literal
 *                         "\n" two-character sequences; they are restored below.
 *   GOOGLE_SHEET_ID       The target spreadsheet's ID (from its URL).
 *
 * Add the `googleapis` package as a Dependency on the Function Service.
 * ---------------------------------------------------------------------------
 */

const { google } = require("googleapis");

const sheets = google.sheets("v4");
const SCOPES = ["https://www.googleapis.com/auth/spreadsheets"];

// Header row to read. Widen if your sheet has more than 172 columns.
const HEADER_RANGE = "A1:FP1";

// Retry once on a transient Sheets API failure before giving up.
const MAX_ATTEMPTS = 2;

/**
 * Reads the required environment variables, failing loudly if any is missing.
 * A missing variable here means the flow silently drops responses, so it is
 * better to throw than to continue.
 * @param {object} context The Twilio Function context.
 * @returns {{clientEmail: string, privateKey: string, sheetId: string}} Config.
 */
function readConfig(context) {
  const missing = [
    "GOOGLE_CLIENT_EMAIL",
    "GOOGLE_PRIVATE_KEY",
    "GOOGLE_SHEET_ID",
  ].filter((name) => !context[name]);

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(", ")}. ` +
        "Set them on the Twilio Function Service, not in this file.",
    );
  }

  return {
    clientEmail: context.GOOGLE_CLIENT_EMAIL,
    // Restore the real newlines that the Twilio env var cannot store.
    privateKey: context.GOOGLE_PRIVATE_KEY.replace(/\\n/g, "\n"),
    sheetId: context.GOOGLE_SHEET_ID,
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
 * @param {string[]} headers The sheet's header values.
 * @param {object} eventData The Function widget's parameters.
 * @returns {{values: Array<Array<string>>}} An append-ready resource.
 */
function buildRow(headers, eventData) {
  const values = headers.map((header) =>
    eventData[header] === undefined || eventData[header] === ""
      ? "No Data"
      : eventData[header],
  );
  return { values: [values] };
}

/**
 * Fetches the sheet's header row.
 * @param {string} accessToken A Google OAuth access token.
 * @param {string} sheetId The target spreadsheet ID.
 * @returns {Promise<string[]>} The header values.
 */
async function getHeaders(accessToken, sheetId) {
  const result = await withRetry(
    () =>
      sheets.spreadsheets.values.get({
        access_token: accessToken,
        spreadsheetId: sheetId,
        range: [HEADER_RANGE],
      }),
    3000,
  );

  const headers = result?.data?.values?.[0];
  if (!headers || headers.length === 0) {
    throw new Error(
      `Sheet ${sheetId} has no header row in ${HEADER_RANGE}; cannot map answers to columns.`,
    );
  }
  return headers;
}

/**
 * Appends one row of answers to the sheet.
 * @param {string} accessToken A Google OAuth access token.
 * @param {string} sheetId The target spreadsheet ID.
 * @param {object} event The Function widget's parameters.
 * @param {string[]} headers The sheet's header values.
 * @returns {Promise<*>} The Sheets API response.
 */
function appendRow(accessToken, sheetId, event, headers) {
  return withRetry(
    () =>
      sheets.spreadsheets.values.append({
        access_token: accessToken,
        spreadsheetId: sheetId,
        range: ["A1"],
        resource: buildRow(headers, event),
        valueInputOption: "RAW",
        insertDataOption: "INSERT_ROWS",
      }),
    3500,
  );
}

exports.handler = async function (context, event, callback) {
  try {
    const config = readConfig(context);

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
        "Service account did not return an access token; check that it has access to the sheet.",
      );
    }

    // Stamp the row's arrival time. Add further derived fields here if needed.
    event.date = new Date().toISOString();

    const headers = await getHeaders(tokens.access_token, config.sheetId);
    await appendRow(tokens.access_token, config.sheetId, event, headers);

    return callback();
  } catch (error) {
    console.error(`publish_gsheets failed: ${error.message}`);
    return callback(error);
  }
};
