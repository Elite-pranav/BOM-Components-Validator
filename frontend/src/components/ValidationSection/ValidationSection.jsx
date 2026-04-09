import { useState, useEffect } from "react";
import DiscrepancyCard from "../DiscrepancyCard/DiscrepancyCard";
import UnresolvedCard from "../UnresolvedCard/UnresolvedCard";
import { getNomenclature, submitValidation, getReportUrl } from "../../api/client";
import { FiSend, FiDownload, FiArrowLeft } from "react-icons/fi";
import styles from "./ValidationSection.module.css";

export default function ValidationSection({ comparison, identifier, onBack }) {
  const [canonicalNames, setCanonicalNames] = useState([]);
  const [decisions, setDecisions] = useState({});
  const [unresolvedMappings, setUnresolvedMappings] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const partsWithIssues = (comparison.parts || []).filter(
    (p) => p.discrepancies.length > 0
  );
  const unresolved = comparison.unresolved || [];

  useEffect(() => {
    getNomenclature()
      .then((data) => setCanonicalNames(data.canonical_names || []))
      .catch(() => {});
  }, []);

  function handleDecision(decision) {
    setDecisions((prev) => ({
      ...prev,
      [decision.canonical_name]: decision,
    }));
  }

  function handleUnresolvedResolve(originalName, canonical) {
    setUnresolvedMappings((prev) => ({ ...prev, [originalName]: canonical }));
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);

    // Build decisions list from both discrepancy decisions and unresolved mappings
    const allDecisions = [
      ...Object.values(decisions),
      ...Object.entries(unresolvedMappings).map(([original, canonical]) => ({
        canonical_name: original,
        action: "disagree",
        mapped_canonical: canonical,
        original_name: original,
      })),
    ];

    try {
      await submitValidation(identifier, allDecisions);
      setSubmitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const totalDecided = Object.keys(decisions).length + Object.keys(unresolvedMappings).length;
  const totalItems = partsWithIssues.length + unresolved.length;

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <button className={styles.backBtn} onClick={onBack}>
          <FiArrowLeft /> Back to Results
        </button>
        <h2 className={styles.heading}>Part Validation</h2>
        <span className={styles.count}>
          {comparison.summary?.discrepancies_found || 0} discrepancies,{" "}
          {unresolved.length} unresolved
        </span>
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {/* Discrepancy cards */}
      {partsWithIssues.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>Discrepancies</h3>
          <div className={styles.cardList}>
            {partsWithIssues.map((part) => (
              <DiscrepancyCard
                key={part.canonical_name}
                part={part}
                canonicalNames={canonicalNames}
                onDecision={handleDecision}
                decision={decisions[part.canonical_name]}
              />
            ))}
          </div>
        </div>
      )}

      {/* Unresolved parts */}
      {unresolved.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>Unresolved Parts</h3>
          <p className={styles.sectionDesc}>
            These parts could not be matched to any known name. Select the correct mapping.
          </p>
          <div className={styles.cardList}>
            {unresolved.map((item, i) => (
              <UnresolvedCard
                key={`${item.source}-${item.original_name}-${i}`}
                item={item}
                canonicalNames={canonicalNames}
                onResolve={handleUnresolvedResolve}
                resolved={unresolvedMappings[item.original_name]}
              />
            ))}
          </div>
        </div>
      )}

      {/* No issues */}
      {partsWithIssues.length === 0 && unresolved.length === 0 && (
        <div className={styles.noIssues}>
          No discrepancies found. All parts match across documents.
        </div>
      )}

      {/* Action bar */}
      <div className={styles.actionBar}>
        <span className={styles.progress}>
          {totalDecided} / {totalItems} reviewed
        </span>
        <div className={styles.actionBtns}>
          {!submitted ? (
            <button
              className={styles.submitBtn}
              onClick={handleSubmit}
              disabled={submitting || totalDecided === 0}
            >
              <FiSend /> {submitting ? "Submitting..." : "Submit Validation"}
            </button>
          ) : (
            <a
              href={getReportUrl(identifier)}
              target="_blank"
              rel="noopener noreferrer"
              className={styles.reportBtn}
            >
              <FiDownload /> Download Report
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
