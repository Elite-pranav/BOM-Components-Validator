import styles from "./Header.module.css";

export default function Header({ onOpenNomenclature }) {
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <div className={styles.logo}>B</div>
        <div className={styles.brand}>
          <h1 className={styles.title}>BOM Components Validator</h1>
          <p className={styles.subtitle}>
            Upload, extract &amp; compare engineering documents
          </p>
        </div>
        {onOpenNomenclature && (
          <button className={styles.nomBtn} onClick={onOpenNomenclature}>
            Nomenclature
          </button>
        )}
      </div>
    </header>
  );
}
