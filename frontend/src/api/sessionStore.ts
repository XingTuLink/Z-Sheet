// Day 15: the Confirm request must re-send the reviewed workbook so the
// server can deterministically re-run its pipeline (it never persists raw
// uploads). sessionStorage only keeps the slim understanding result, so the
// actual Files live here in module memory between Upload and Understanding.
// A page refresh releases them — the Understanding page then asks the user to
// re-upload, which also resets the review state.

let uploadedFiles: File[] = []

export function setLastUploadedFiles(files: File[]): void {
  uploadedFiles = files
}

export function getLastUploadedFiles(): File[] {
  return uploadedFiles
}

export function setLastUploadedFile(file: File | null): void {
  uploadedFiles = file ? [file] : []
}

export function getLastUploadedFile(): File | null {
  return uploadedFiles.length === 1 ? uploadedFiles[0] : null
}
