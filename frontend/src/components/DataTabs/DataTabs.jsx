import { useState } from "react";
import DataTable from "../DataTable/DataTable";
import styles from "./DataTabs.module.css";

// ── Column definitions ─────────────────────────────────────────────────────
// Matches cs_bom.json: [{ref, description, qty, material}]
const CS_COLUMNS = [
  { key: "ref",         label: "Ref" },
  { key: "description", label: "Description" },
  { key: "qty",         label: "Qty" },
  { key: "material",    label: "Material" },
];

// Matches bom_data.json: [{item_number, component_number, description,
//                          quantity, unit, text1, text2, sort_string}]
const BOM_COLUMNS = [
  { key: "item_number",      label: "Item #" },
  { key: "component_number", label: "Component #" },
  { key: "description",      label: "Description" },
  { key: "quantity",         label: "Qty" },
  { key: "unit",             label: "Unit" },
  { key: "text1",            label: "Usage" },
  { key: "sort_string",      label: "Category" },
];

// Matches sap_data.json entries: [{key, value}]
const SAP_COLUMNS = [
  { key: "key",   label: "Field" },
  { key: "value", label: "Value" },
];

const TABS = [
  { id: "cs",  label: "CS Drawing BOM" },
  { id: "bom", label: "Excel BOM" },
  { id: "sap", label: "SAP Data" },
];

// ── Component ──────────────────────────────────────────────────────────────

export default function DataTabs({ results, activeTab, onTabChange }) {
  function getActiveData() {
    if (activeTab === "cs") {
      return { columns: CS_COLUMNS, rows: results.cs_bom || [] };
    }

    if (activeTab === "bom") {
      return { columns: BOM_COLUMNS, rows: results.bom_excel || [] };
    }

    if (activeTab === "sap") {
      // sap_data.json shape: { entries: [{key, value}], design_text: "..." }
      const entries = results.sap_data?.entries || [];
      return { columns: SAP_COLUMNS, rows: entries };
    }

    return { columns: [], rows: [] };
  }

  const { columns, rows } = getActiveData();

  return (
    <div className={styles.container}>
      <div className={styles.tabBar}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`${styles.tab} ${activeTab === tab.id ? styles.active : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Design text banner — only shown on SAP tab when present */}
      {activeTab === "sap" && results.sap_data?.design_text && (
        <div className={styles.designText}>
          <strong>Design Notes: </strong>
          {results.sap_data.design_text}
        </div>
      )}

      <DataTable columns={columns} rows={rows} />
    </div>
  );
}

// ── Export for ActionBar ───────────────────────────────────────────────────

export function getTabData(results, activeTab) {
  if (activeTab === "cs")  return results.cs_bom || [];
  if (activeTab === "bom") return results.bom_excel || [];
  if (activeTab === "sap") return results.sap_data?.entries || [];
  return [];
}