# Required deployment fix

The reported installation failure was caused by Streamlit Community Cloud
creating a **Python 3.14.7** environment. `pyarrow==21.0.0` has no matching
Python 3.14 wheel, so the platform tried to compile Apache Arrow and failed.

## Correct procedure

1. Push this corrected project to the GitHub app repository.
2. Copy the existing Streamlit app's secrets and deployment settings.
3. Delete the failed app from Streamlit Community Cloud.
4. Create the app again from the same repository, branch, and `app.py`.
5. Open **Advanced settings** and choose **Python 3.12**.
6. Paste the secrets, retain the same custom subdomain if required, and deploy.

Changing only `requirements.txt` cannot change the Python version of an app
that has already been deployed.
