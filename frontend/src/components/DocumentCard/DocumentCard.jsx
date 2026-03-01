import { FiFileText, FiFile, FiEye, FiDownload } from "react-icons/fi";
import styles from "./DocumentCard.module.css";

const ICONS = {
  cs: FiFileText,
  bom: FiFile,
  sap: FiFileText,
};

export default function DocumentCard({ docType, label, previewUrl, downloadUrl }) {
  const Icon = ICONS[docType] || FiFile;

  return (
    <div className={styles.card}>
      <Icon className={styles.icon} />
      <span className={styles.label}>{label}</span>
      <div className={styles.actions}>
        <a
          href={previewUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={styles.btn}
          title="Preview in new tab"
        >
          <FiEye />
        </a>
        <a
          href={downloadUrl}
          className={styles.btn}
          title="Download"
        >
          <FiDownload />
        </a>
      </div>
    </div>
  );
}
