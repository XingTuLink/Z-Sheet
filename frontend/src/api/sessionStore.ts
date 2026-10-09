// Day 15: the Confirm request must re-send the reviewed workbook so the
// server can deterministically re-run its pipeline (it never persists raw
// uploads). sessionStorage only keeps the slim understanding result, so the
// actual File lives here in module memory between Upload and Understanding.
// A page refresh releases it — the Understanding page then asks the user to
// re-upload, which also resets the review state.

let lastUploadedFile: File | null = null

export function setLastUploadedFile(file: File | null): void {
  lastUploadedFile = file
}

export function getLastUploadedFile(): File | null {
  return lastUploadedFile
}
