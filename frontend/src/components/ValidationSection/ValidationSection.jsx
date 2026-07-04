import { useState, useEffect } from "react";
import DiscrepancyCard from "../DiscrepancyCard/DiscrepancyCard";
import UnresolvedCard from "../UnresolvedCard/UnresolvedCard";
import { fetchNomenclature, submitValidation, getReportUrl } from "../../api/client";
import { FiSend, FiDownload, FiArrowLeft } from "react-icons/fi";
import styles from "./ValidationSection.module.css";

export default function ValidationSection({ comparison, identifier, onBack }) {
  const [canonicalNames, setCanonicalNames] = useState([]);
  const [decisions, setDecisions] = useState({});         // canonical_name → decision object | null
  const [unresolvedMappings, setUnresolvedMappings] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const partsWithIssues = (comparison.parts || []).filter((p) => p.discrepancies.length > 0);
  const unresolved = comparison.unresolved || [];

  useEffect(() => {
    fetchNomenclature()
      .then((data) => setCanonicalNames((data.parts || []).map((p) => p.canonical)))
      .catch(() => {});
  }, []);

  function handleDecision(decision) {
    if (decision === null) {
      // undo — this shouldn't happen at the top level, DiscrepancyCard passes null on undo
      return;
    }
    setDecisions((prev) => ({
      ...prev,
      [decision.canonical_name]: decision,
    }));
  }

  function handleUndo(canonicalName) {
    setDecisions((prev) => {
      const next = { ...prev };
      delete next[canonicalName];
      return next;
    });
  }

  function handleUnresolvedResolve(originalName, canonical) {
    setUnresolvedMappings((prev) => ({ ...prev, [originalName]: canonical }));
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);

    // Collect all explicit decisions (agree / disagree / ignore)
    const allDecisions = [
      ...Object.values(decisions).filter(Boolean),
      ...Object.entries(unresolvedMappings).map(([original, canonical]) => ({
        canonical_name:   original,
        action:           "disagree",
        mapped_canonical: canonical,
        original_name:    original,
      })),
    ];

    // Parts that have a discrepancy but no decision → unresolved
    const unresolvedDiscrepancies = partsWithIssues
      .filter((p) => !decisions[p.canonical_name])
      .map((p) => ({
        canonical_name: p.canonical_name,
        action:         "unresolved",
      }));

    try {
      await submitValidation(identifier, [...allDecisions, ...unresolvedDiscrepancies]);
      setSubmitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  // Counts
  const nAgreed     = Object.values(decisions).filter((d) => d?.action === "agree").length;
  const nDismissed  = Object.values(decisions).filter((d) => d?.action === "disagree").length;
  const nIgnored    = Object.values(decisions).filter((d) => d?.action === "ignore").length;
  const nDecided    = nAgreed + nDismissed + nIgnored + Object.keys(unresolvedMappings).length;
  const nTotal      = partsWithIssues.length + unresolved.length;
  const nPending    = nTotal - nDecided;

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

      {/* Progress summary */}
      {nTotal > 0 && (
        <div className={styles.progressBar}>
          <div className={styles.progressStats}>
            {nAgreed > 0    && <span className={styles.statConfirmed}>{nAgreed} confirmed</span>}
            {nDismissed > 0 && <span className={styles.statDismissed}>{nDismissed} dismissed</span>}
            {nIgnored > 0   && <span className={styles.statIgnored}>{nIgnored} ignored</span>}
            {nPending > 0   && <span className={styles.statPending}>{nPending} pending</span>}
          </div>
          <div className={styles.progressTrack}>
            <div className={styles.progressFill} style={{ width: `${(nDecided / nTotal) * 100}%` }} />
          </div>
        </div>
      )}

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
                onDecision={(d) => {
                  if (d === null) {
                    handleUndo(part.canonical_name);
                  } else {
                    handleDecision(d);
                  }
                }}
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
        <div className={styles.submitNote}>
          {nPending > 0
            ? `${nPending} item${nPending !== 1 ? "s" : ""} not reviewed — will be marked unresolved in the report.`
            : "All items reviewed."}
        </div>
        <div className={styles.actionBtns}>
          {!submitted ? (
            <button
              className={styles.submitBtn}
              onClick={handleSubmit}
              disabled={submitting}
            >
              <FiSend /> {submitting ? "Submitting…" : "Submit Validation"}
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
