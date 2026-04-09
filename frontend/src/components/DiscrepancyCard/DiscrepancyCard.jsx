import { useState } from "react";
import { FiCheck, FiX, FiAlertTriangle, FiChevronDown } from "react-icons/fi";
import styles from "./DiscrepancyCard.module.css";

const SOURCE_LABELS = { cs: "CS Drawing", bom: "Excel BOM", sap: "SAP Data" };

export default function DiscrepancyCard({
  part,
  canonicalNames,
  onDecision,
  decision,
}) {
  const [showDropdown, setShowDropdown] = useState(false);
  const [search, setSearch] = useState("");

  const filteredNames = canonicalNames.filter((n) =>
    n.toLowerCase().includes(search.toLowerCase())
  );

  function handleDisagree() {
    setShowDropdown(true);
  }

  function handleMapTo(canonical) {
    onDecision({
      canonical_name: part.canonical_name,
      action: "disagree",
      mapped_canonical: canonical,
      original_name: part.canonical_name,
    });
    setShowDropdown(false);
  }

  function handleAgree(discIndex) {
    onDecision({
      canonical_name: part.canonical_name,
      discrepancy_index: discIndex,
      action: "agree",
    });
  }

  const isDecided = !!decision;

  return (
    <div
      className={`${styles.card} ${
        decision?.action === "agree"
          ? styles.agreed
          : decision?.action === "disagree"
            ? styles.dismissed
            : ""
      }`}
    >
      <div className={styles.header}>
        <h4 className={styles.partName}>{part.canonical_name}</h4>
        <div className={styles.presence}>
          {["cs", "bom", "sap"].map((src) => (
            <span
              key={src}
              className={`${styles.badge} ${
                part[src]?.present ? styles.present : styles.missing
              }`}
              title={SOURCE_LABELS[src]}
            >
              {src.toUpperCase()}
            </span>
          ))}
        </div>
      </div>

      {/* Material comparison */}
      <div className={styles.materials}>
        {["cs", "bom", "sap"].map((src) => (
          <div key={src} className={styles.matRow}>
            <span className={styles.matLabel}>{SOURCE_LABELS[src]}:</span>
            <span className={styles.matValue}>
              {part[src]?.material || (part[src]?.present ? "—" : "Not present")}
            </span>
          </div>
        ))}
      </div>

      {/* Discrepancy details */}
      {part.discrepancies.map((disc, i) => (
        <div key={i} className={styles.discrepancy}>
          <FiAlertTriangle className={styles.warnIcon} />
          <span className={styles.discType}>{disc.type}</span>
          <span className={styles.discDetail}>{disc.detail}</span>
        </div>
      ))}

      {/* Action buttons */}
      {!isDecided && (
        <div className={styles.actions}>
          <button
            className={styles.agreeBtn}
            onClick={() => handleAgree(0)}
          >
            <FiCheck /> Agree (Confirm Error)
          </button>
          <button className={styles.disagreeBtn} onClick={handleDisagree}>
            <FiX /> Disagree (Same Part)
          </button>
        </div>
      )}

      {/* Decision status */}
      {isDecided && (
        <div className={styles.decisionStatus}>
          {decision.action === "agree"
            ? "Confirmed as error"
            : `Mapped to: ${decision.mapped_canonical}`}
        </div>
      )}

      {/* Dropdown for mapping */}
      {showDropdown && (
        <div className={styles.dropdown}>
          <p className={styles.dropdownLabel}>
            Select the correct canonical name:
          </p>
          <input
            className={styles.searchInput}
            type="text"
            placeholder="Search parts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className={styles.dropdownList}>
            {filteredNames.map((name) => (
              <button
                key={name}
                className={styles.dropdownItem}
                onClick={() => handleMapTo(name)}
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
