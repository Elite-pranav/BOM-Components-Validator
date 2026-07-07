import { useState } from "react";
import styles from "./UnresolvedCard.module.css";

const SOURCE_LABELS = { cs: "CS Drawing", bom: "Excel BOM", sap: "SAP Data" };

export default function UnresolvedCard({ item, canonicalNames, onResolve, resolved }) {
  const [search, setSearch] = useState("");

  const filteredNames = canonicalNames.filter((n) =>
    n.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className={`${styles.card} ${resolved ? styles.resolved : ""}`}>
      <div className={styles.header}>
        <span className={styles.source}>{SOURCE_LABELS[item.source]}</span>
        <span className={styles.name}>{item.original_name}</span>
      </div>

      {resolved ? (
        <p className={styles.resolvedText}>Mapped to: {resolved}</p>
      ) : (
        <div className={styles.mapSection}>
          <input
            className={styles.searchInput}
            type="text"
            placeholder="Search canonical names..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className={styles.list}>
            {filteredNames.map((name) => (
              <button
                key={name}
                className={styles.item}
                onClick={() => onResolve(item.original_name, name)}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
