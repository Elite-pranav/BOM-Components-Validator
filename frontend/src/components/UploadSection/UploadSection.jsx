import FileDropZone from "../FileDropZone/FileDropZone";
import styles from "./UploadSection.module.css";

export default function UploadSection({ files, onFileChange, onExtract, loading }) {
  const allSelected = files.cs && files.bom && files.sap;

  return (
    <section className={styles.section}>
      <h2 className={styles.heading}>Upload Documents</h2>
      <p className={styles.description}>
        Select the three documents for extraction: Cross-Section PDF, BOM Excel, and SAP Data PDF.
      </p>

      <div className={styles.grid}>
        <FileDropZone
          label="Cross-Section PDF"
          accept=".pdf"
          file={files.cs}
          onFileSelect={(f) => onFileChange("cs", f)}
        />
        <FileDropZone
          label="BOM Excel (.XLSX)"
          accept=".xlsx,.xls"
          file={files.bom}
          onFileSelect={(f) => onFileChange("bom", f)}
        />
        <FileDropZone
          label="SAP Data PDF"
          accept=".pdf"
          file={files.sap}
          onFileSelect={(f) => onFileChange("sap", f)}
        />
      </div>

      <button
        className={styles.extractBtn}
        disabled={!allSelected || loading}
        onClick={onExtract}
      >
        {loading ? "Extracting..." : "Start Extraction"}
      </button>
    </section>
  );
}
