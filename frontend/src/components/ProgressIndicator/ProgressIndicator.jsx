import styles from "./ProgressIndicator.module.css";

export default function ProgressIndicator() {
  return (
    <div className={styles.container}>
      <div className={styles.spinner} />
      <p className={styles.text}>Running extraction pipeline...</p>
      <p className={styles.sub}>
        Processing CS drawing, BOM spreadsheet, and SAP data concurrently
      </p>
    </div>
  );
}
