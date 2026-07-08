import { useState } from "react";
import { FiExternalLink } from "react-icons/fi";
import styles from "./UnresolvedCard.module.css";

const SOURCE_LABELS = { cs: "CS Drawing", bom: "Excel BOM", sap: "SAP Data" };

export default function UnresolvedCard({ item, canonicalNames, onResolve, resolved, onOpenNomenclature }) {
  const [search, setSearch] = useState("");

  const filteredNames = canonicalNames.filter((n) =>
    n.toLowerCase().includes(search.toLowerCase())
  );

  const noResults = search.length > 0 && filteredNames.length === 0;

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

          {noResults ? (
            <div className={styles.emptyHint}>
              <span>No match for "{search}".</span>
              {onOpenNomenclature && (
                <button className={styles.addLink} onClick={onOpenNomenclature}>
                  Add it in Nomenclature Manager <FiExternalLink />
                </button>
              )}
            </div>
          ) : (
            onOpenNomenclature && (
              <div className={styles.subtleHint}>
                Part missing from the list?{" "}
                <button className={styles.subtleLink} onClick={onOpenNomenclature}>
                  Open Nomenclature Manager
                </button>
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}
