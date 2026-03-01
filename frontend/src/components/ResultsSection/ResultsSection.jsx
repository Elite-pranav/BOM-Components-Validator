import { useState } from "react";
import SummaryCards from "../SummaryCards/SummaryCards";
import DataTabs from "../DataTabs/DataTabs";
import ActionBar from "../ActionBar/ActionBar";
import DocumentPreview from "../DocumentPreview/DocumentPreview";
import styles from "./ResultsSection.module.css";

export default function ResultsSection({ results, identifier, onReset }) {
  const [activeTab, setActiveTab] = useState("cs");

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.heading}>
          Extraction Results
          <span className={styles.id}>{identifier}</span>
        </h2>
        <button className={styles.resetBtn} onClick={onReset}>
          New Extraction
        </button>
      </div>

      <SummaryCards results={results} />
      <DataTabs results={results} activeTab={activeTab} onTabChange={setActiveTab} />
      <ActionBar results={results} activeTab={activeTab} identifier={identifier} />
      <DocumentPreview identifier={identifier} />
    </div>
  );
}
