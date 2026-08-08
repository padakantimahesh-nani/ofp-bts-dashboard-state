# OFP / BBZ BTS Sales Pivot Dashboard

This Streamlit application provides an Excel-style pivot builder over two analytical models:

- **YOY Sales** — This Year and Last Year sales aggregated independently to `BTS WEEK + DAY + Code + Item Barcode`, then outer-joined for a side-by-side comparison.
- **SOH** — the supplied week-on-week stock snapshot, retained as its own model so its already-prepared WTD/MTD/YTD measures are not multiplied by a sales join.

Location Master is always joined using **Code**. `Location Code` is retained as `LM Location Code` for reference and is never a join key.

## 1. Create the private persistence repository

1. Create a private GitHub repository, for example `ofp-bts-dashboard-state`.
2. Add a `README.md` so the default branch exists.
3. Create a fine-grained GitHub personal access token scoped only to that repository, with **Contents: Read and write** permission.
4. Do not put sales files or secrets in this repository. The app writes only `credentials.yaml`, `app_config.json`, and small JSON files under `saved_views/`.

## 2. Prepare Google Drive

Keep all five source files publicly readable. For the three large fact tables, use Parquet or CSV. In Google Drive, open each file and copy the file ID from its URL. The admin can maintain IDs and filenames in **Admin Panel → Data Sources**.

The filename extension controls the parser, so it must match the real format. Required source keys are:

- `this_year_sales`
- `last_year_sales`
- `week_on_week_soh`
- `location_master`
- `calendar`

Important: because the Drive folder/files are public, anyone with their public link can access the raw files. Dashboard login protects only the dashboard UI; it does not protect public Drive content.

## 3. Configure Streamlit secrets

For local development, create `.streamlit/secrets.toml`. For Streamlit Community Cloud, paste the same values into **App settings → Secrets**. Never commit this file.

```toml
[github]
token = "github_pat_..."
owner = "YOUR_GITHUB_OWNER"
repo = "ofp-bts-dashboard-state"
branch = "main"

[drive]
folder_url = "YOUR_PUBLIC_GOOGLE_DRIVE_FOLDER_URL"

[drive.files.this_year_sales]
file_id = "DRIVE_FILE_ID"
filename = "This Year Sales.parquet"

[drive.files.last_year_sales]
file_id = "DRIVE_FILE_ID"
filename = "Last Year Sales.parquet"

[drive.files.week_on_week_soh]
file_id = "DRIVE_FILE_ID"
filename = "Week on week SOH.parquet"

[drive.files.location_master]
file_id = "DRIVE_FILE_ID"
filename = "Location Master.xlsx"

[drive.files.calendar]
file_id = "DRIVE_FILE_ID"
filename = "Calender.xlsx"
```

The `[drive.files...]` blocks are optional if the admin will enter all IDs after login. `folder_url` is informational; downloads use explicit public file IDs to avoid scanning and downloading an entire large folder on the 1 GB tier.

## 4. Run locally

Use **Python 3.12**. This project intentionally pins PyArrow 21, which has a
prebuilt Linux wheel for Python 3.12. Do not deploy it with Python 3.14.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## 5. Deploy to Streamlit Community Cloud

1. Put these application files in a GitHub repository (this can be separate from the private state repository).
2. In Streamlit Community Cloud, create an app using `app.py` as the entry point.
3. Before deploying, open **Advanced settings** and select **Python 3.12**.
4. Add the secrets shown above.
5. Deploy. The local disk is treated as ephemeral; durable credentials, app configuration, and saved views are stored through the private state repository.

### Fixing an app previously created with Python 3.14

Streamlit Community Cloud does not allow an existing app's Python version to
be changed in place. Record its repository, branch, entrypoint, URL and secrets;
then delete that app and create it again. During creation, choose **Python 3.12**
under **Advanced settings**. Reusing the same custom subdomain restores the same
public app address.

## 6. First login

On the first run, if `credentials.yaml` does not exist, the app seeds:

| Username | Temporary password | Role |
|---|---|---|
| `OFP_Admin` | `OFP_ADMIN` | admin |
| `OFP_User` | `Welcome@123` | user |

Both accounts are forced to set a new password before accessing the application.

After changing the admin password:

1. Open **Data Sources**, verify all five file IDs/filenames, and click **Refresh Data from Drive**.
2. Check the returned row counts.
3. Open **Pivot Configuration**, choose the fields users may see, confirm the default view, and save.
4. Create named users and issue temporary passwords from **User Management**.

The seeded default pivot is `Rows = BTS WEEK` and `Values = TY NSQ, LY NSQ`, both using Sum.

## Fixed management panels and Report Date

The user report area includes five fixed interactive panels in addition to the
Custom Pivot Builder:

1. Department-wise BTS Performance
2. Skechers Review (colour and size)
3. BTS Type Inventory Review
4. Top 10 Brands Review
5. Store-wise Performance

Every panel provides Excel-style multi-select **button slicers**, **Clear Selection**, and CSV export. Admin users
can open **Admin Panel → Report Settings** and save one global **Report Date**.
TY is capped at that actual date; LY is capped at the matching BTS week/day
position because the two campaigns are not calendar-date aligned. SOH uses the
matching BTS-week snapshot.

Panels 1, 4, and 5 are permanently limited to the approved BTS Types
`BACKPACK` and `KIDS FOOTWEAR`; their BTS Type slicers display only those two
buttons. Panel 2 is permanently limited to `KIDS FOOTWEAR`. Clearing a slicer
removes the active selection but does not allow unrelated BTS Types into these
reports. Panel 5 additionally includes only codes mapped in Location Master as
`Type = Store`; it never substitutes the SOH fact's `LOC Type` for this rule.

Calculated measures follow the approved definitions:

- NSQ Growth = `TY NSQ / LY NSQ - 1`
- NSV Growth = `TY NSV / LY NSV - 1`
- TY/LY GM% = `1 - COGS / NSV`
- GMV Growth = `(TY NSV - TY COGS) / (LY NSV - LY COGS) - 1`
- WROS = `TY NSQ / (Report Date - Calendar Starting Date) * 7`
- WOC = `Inventory Qty / WROS`
- TY/LY ASP = `NSV / NSQ`
- ST% = `TY NSQ / (TY NSQ + Inventory Qty)`

Zero denominators return blank rather than an error.

Fixed pivot tables use only the approved reference palette: light blue headers
and grand totals, pale green country subtotals on the inventory matrix, white
detail rows, and blue subtotal borders. Selected slicer buttons use the same
light blue; unselected buttons remain white with a dark outline.

## Operational notes

- Data loading is cached for 30 minutes. **Refresh Data from Drive** clears the cache immediately.
- Percentage variance and margin fields return blank/NaN when their denominator is zero.
- The admin inclusion switches do not delete or alter source data; they filter the in-memory model before users pivot.
- Saved views contain only field selections, aggregations, and filter choices—never raw fact data.
- For Community Cloud memory limits, Parquet is strongly preferred. If files remain too large, pre-partition or pre-aggregate upstream rather than using XLSX.
