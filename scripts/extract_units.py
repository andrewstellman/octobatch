#!/usr/bin/env python3
"""
extract_units.py - Universal unit extractor for batch processing runs.

Extracts validated units to individual files with optional transformation
and upload to cloud storage.

Usage:
    python scripts/extract_units.py --run-dir runs/run_001 --step-name extract_units --config config/config.yaml

Step config structure:
    - name: extract_units
      scope: run
      script: scripts/extract_units.py
      output_dir: "outputs/units"
      filename_expression: "data['unit_id']"
      content_expression: "data"  # optional, defaults to full record
      compression: gzip  # none, gzip
      upload:
        provider: google_drive
        folder_id: "abc123"
        credentials_env: GDRIVE_CREDENTIALS
        delete_local: true
"""

import argparse
import gzip
import json
import os
import shutil
import sys
from pathlib import Path

from octobatch_utils import load_manifest, load_jsonl, load_config, create_interpreter


def get_final_validated_files(run_dir: Path, manifest: dict) -> list[Path]:
    """
    Get all validated files from the final chunk-scope step.

    Returns list of paths to *_validated.jsonl files from the last pipeline step.
    """
    pipeline = manifest.get("pipeline", [])
    if not pipeline:
        return []

    last_step = pipeline[-1]
    chunks_dir = run_dir / "chunks"

    if not chunks_dir.exists():
        return []

    validated_files = []
    for chunk_dir in chunks_dir.iterdir():
        if not chunk_dir.is_dir():
            continue
        validated_file = chunk_dir / f"{last_step}_validated.jsonl"
        if validated_file.exists():
            validated_files.append(validated_file)

    return validated_files


def extract_units(
    run_dir: Path,
    step_config: dict
) -> dict:
    """
    Extract validated units to individual files.

    Args:
        run_dir: Path to the run directory
        step_config: Step configuration dict

    Returns:
        Dict with extraction summary
    """
    # Get config options
    output_dir = Path(step_config.get("output_dir", "outputs/units"))
    filename_expr = step_config.get("filename_expression", "data['unit_id']")
    content_expr = step_config.get("content_expression", "data")
    compression = step_config.get("compression", "none")
    upload_config = step_config.get("upload", {})

    # Make output_dir relative to run_dir if not absolute
    if not output_dir.is_absolute():
        output_dir = run_dir / output_dir

    # Clear output directory for idempotency (safe to re-run)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load manifest
    manifest = load_manifest(run_dir)

    # Get validated files
    validated_files = get_final_validated_files(run_dir, manifest)

    if not validated_files:
        print("No validated files found", file=sys.stderr)
        return {
            "extracted": 0,
            "errors": 0,
            "output_dir": str(output_dir)
        }

    # Create interpreter for expression evaluation
    aeval = create_interpreter()

    extracted_count = 0
    error_count = 0
    output_files = []

    for validated_file in validated_files:
        records = load_jsonl(validated_file)

        for record in records:
            try:
                # Set record as 'data' in interpreter
                aeval.symtable['data'] = record

                # Evaluate filename expression
                filename = aeval(filename_expr)
                if aeval.error:
                    print(f"Error evaluating filename expression: {aeval.error}", file=sys.stderr)
                    aeval.error = []
                    error_count += 1
                    continue

                if not filename:
                    print(f"Empty filename for record: {record.get('unit_id', 'unknown')}", file=sys.stderr)
                    error_count += 1
                    continue

                # Sanitize filename
                filename = str(filename).replace("/", "_").replace("\\", "_")

                # Evaluate content expression
                content = aeval(content_expr)
                if aeval.error:
                    print(f"Error evaluating content expression: {aeval.error}", file=sys.stderr)
                    aeval.error = []
                    error_count += 1
                    continue

                # Determine file extension and write
                if compression == "gzip":
                    output_path = output_dir / f"{filename}.json.gz"
                    with gzip.open(output_path, 'wt', encoding='utf-8') as f:
                        json.dump(content, f, indent=2)
                else:
                    output_path = output_dir / f"{filename}.json"
                    with open(output_path, 'w') as f:
                        json.dump(content, f, indent=2)

                output_files.append(output_path)
                extracted_count += 1

            except Exception as e:
                print(f"Error processing record: {e}", file=sys.stderr)
                error_count += 1

    # Handle upload if configured
    uploaded_count = 0
    if upload_config and output_files:
        uploaded_count = upload_files(output_files, upload_config)

        # Delete local files if configured
        if upload_config.get("delete_local") and uploaded_count == len(output_files):
            for f in output_files:
                try:
                    f.unlink()
                except Exception as e:
                    print(f"Error deleting {f}: {e}", file=sys.stderr)

        # Warn if uploads were configured but all failed
        if uploaded_count == 0:
            print(f"Warning: All Google Drive uploads failed. Files saved locally to {output_dir}", file=sys.stderr)

    return {
        "extracted": extracted_count,
        "errors": error_count,
        "output_dir": str(output_dir),
        "uploaded": uploaded_count if upload_config else None
    }


def upload_files(files: list[Path], upload_config: dict) -> int:
    """
    Upload files to configured cloud storage.

    Args:
        files: List of file paths to upload
        upload_config: Upload configuration dict

    Returns:
        Number of files successfully uploaded
    """
    provider = upload_config.get("provider", "")

    if provider == "google_drive":
        return upload_to_google_drive(files, upload_config)
    else:
        print(f"Unknown upload provider: {provider}", file=sys.stderr)
        return 0


def upload_to_google_drive(files: list[Path], upload_config: dict) -> int:
    """
    Upload files to Google Drive.

    Args:
        files: List of file paths to upload
        upload_config: Upload configuration with folder_id and credentials_env

    Returns:
        Number of files successfully uploaded
    """
    # Check for required libraries
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("Warning: Google Drive upload skipped. To enable, run: pip install google-api-python-client google-auth", file=sys.stderr)
        return 0

    folder_id = upload_config.get("folder_id")
    credentials_env = upload_config.get("credentials_env", "GDRIVE_CREDENTIALS")

    if not folder_id:
        print("Warning: No folder_id configured for Google Drive upload", file=sys.stderr)
        return 0

    # Get credentials from environment variable
    credentials_path = os.environ.get(credentials_env)
    if not credentials_path:
        print(f"Warning: Environment variable {credentials_env} not set, skipping upload", file=sys.stderr)
        return 0

    if not Path(credentials_path).exists():
        print(f"Warning: Credentials file not found: {credentials_path}, skipping upload", file=sys.stderr)
        return 0

    try:
        # Authenticate
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        service = build('drive', 'v3', credentials=credentials)

        uploaded_count = 0
        for file_path in files:
            try:
                # Determine MIME type
                if file_path.suffix == '.gz':
                    mime_type = 'application/gzip'
                else:
                    mime_type = 'application/json'

                file_metadata = {
                    'name': file_path.name,
                    'parents': [folder_id]
                }

                media = MediaFileUpload(
                    str(file_path),
                    mimetype=mime_type,
                    resumable=True
                )

                service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id',
                    supportsAllDrives=True  # Required for Shared Drives
                ).execute()

                uploaded_count += 1

            except Exception as e:
                error_str = str(e)
                # Check for storage quota exceeded - common with service accounts
                if "storageQuotaExceeded" in error_str:
                    print(f"Warning: Google Drive upload failed - Service accounts cannot upload to regular Drive folders.", file=sys.stderr)
                    print(f"Solution: Use a Shared Drive (Google Workspace) instead of a personal Drive folder.", file=sys.stderr)
                    print(f"See: https://developers.google.com/workspace/drive/api/guides/about-shareddrives", file=sys.stderr)
                else:
                    print(f"Error uploading {file_path.name}: {e}", file=sys.stderr)

        return uploaded_count

    except Exception as e:
        error_str = str(e)
        if "storageQuotaExceeded" in error_str:
            print(f"Warning: Google Drive upload failed - Service accounts cannot upload to regular Drive folders.", file=sys.stderr)
            print(f"Solution: Use a Shared Drive (Google Workspace) instead of a personal Drive folder.", file=sys.stderr)
            print(f"See: https://developers.google.com/workspace/drive/api/guides/about-shareddrives", file=sys.stderr)
        else:
            print(f"Error initializing Google Drive client: {e}", file=sys.stderr)
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract validated units to individual files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Extract units (called by orchestrator)
    python scripts/extract_units.py --run-dir runs/run_001 --step-name extract_units --config config/config.yaml
        """
    )

    parser.add_argument(
        "--run-dir", "-r",
        type=Path,
        required=True,
        help="Path to run directory"
    )

    parser.add_argument(
        "--step-name", "-s",
        type=str,
        required=True,
        help="Step name in pipeline config"
    )

    parser.add_argument(
        "--config", "-c",
        type=Path,
        required=True,
        help="Path to config file"
    )

    args = parser.parse_args()

    # Validate run directory
    if not args.run_dir.exists():
        print(f"Error: Run directory not found: {args.run_dir}", file=sys.stderr)
        sys.exit(1)

    # Load config and find step
    if not args.config.exists():
        print(f"Error: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    # Find step config
    step_config = None
    steps = config.get("pipeline", {}).get("steps", [])
    for step in steps:
        if step.get("name") == args.step_name:
            step_config = step
            break

    if step_config is None:
        print(f"Error: Step '{args.step_name}' not found in config", file=sys.stderr)
        sys.exit(1)

    # Extract units
    try:
        result = extract_units(args.run_dir, step_config)
    except Exception as e:
        print(f"Error extracting units: {e}", file=sys.stderr)
        sys.exit(1)

    # Print summary
    print(f"Extracted {result['extracted']} units to {result['output_dir']}")
    if result['errors'] > 0:
        print(f"Errors: {result['errors']}")
    if result.get('uploaded') is not None:
        print(f"Uploaded: {result['uploaded']}")

    sys.exit(0 if result['errors'] == 0 else 1)


if __name__ == "__main__":
    main()
