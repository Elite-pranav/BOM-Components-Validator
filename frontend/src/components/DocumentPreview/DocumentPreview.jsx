import DocumentCard from "../DocumentCard/DocumentCard";
import { getDocumentUrl } from "../../api/client";
import styles from "./DocumentPreview.module.css";

const DOCUMENTS = [
  { type: "cs", label: "Cross-Section PDF" },
  { type: "bom", label: "BOM Excel Spreadsheet" },
  { type: "sap", label: "SAP Data PDF" },
];

export default function DocumentPreview({ identifier }) {
  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>Uploaded Documents</h3>
      <div className={styles.grid}>
        {DOCUMENTS.map((doc) => (
          <DocumentCard
            key={doc.type}
            docType={doc.type}
            label={doc.label}
            previewUrl={getDocumentUrl(identifier, doc.type)}
            downloadUrl={getDocumentUrl(identifier, doc.type, true)}
          />
        ))}
      </div>
    </div>
  );
}
