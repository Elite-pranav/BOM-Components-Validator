import { useState } from "react";
import DataTable from "../DataTable/DataTable";
import styles from "./DataTabs.module.css";

const CS_COLUMNS = [
  { key: "ref", label: "Ref" },
  { key: "description", label: "Description" },
  { key: "qty", label: "Qty" },
  { key: "material", label: "Material" },
];

const BOM_COLUMNS = [
  { key: "item_number", label: "Item #" },
  { key: "component_number", label: "Component #" },
  { key: "description", label: "Description" },
  { key: "part_type", label: "Part Type" },
  { key: "quantity", label: "Qty" },
  { key: "unit", label: "Unit" },
  { key: "material", label: "Material" },
  { key: "coating", label: "Coating" },
  { key: "category", label: "Category" },
];

const SAP_PARTS_COLUMNS = [
  { key: "name", label: "Part" },
  { key: "raw", label: "Raw Value" },
  { key: "material", label: "Material Code" },
  { key: "coating", label: "Coating" },
];

const SAP_META_COLUMNS = [
  { key: "key", label: "Field" },
  { key: "value", label: "Value" },
];

const TABS = [
  { id: "cs", label: "CS Drawing BOM" },
  { id: "bom", label: "Excel BOM" },
  { id: "sap", label: "SAP Data" },
];

export default function DataTabs({ results, activeTab, onTabChange }) {
  const [sapSubTab, setSapSubTab] = useState("parts");

  function getActiveData() {
    if (activeTab === "cs") {
      return { columns: CS_COLUMNS, rows: results.cs_bom || [] };
    }
    if (activeTab === "bom") {
      return { columns: BOM_COLUMNS, rows: results.bom_excel || [] };
    }
    if (activeTab === "sap") {
      const sapData = results.sap_data || {};
      if (sapSubTab === "parts") {
        const parts = sapData.parts || {};
        const rows = Object.entries(parts).map(([name, data]) => ({
          name,
          ...data,
        }));
        return { columns: SAP_PARTS_COLUMNS, rows };
      } else {
        const meta = sapData.metadata || {};
        const rows = Object.entries(meta).map(([key, value]) => ({
          key,
          value,
        }));
        return { columns: SAP_META_COLUMNS, rows };
      }
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

      {activeTab === "sap" && (
        <div className={styles.subTabBar}>
          <button
            className={`${styles.subTab} ${sapSubTab === "parts" ? styles.subActive : ""}`}
            onClick={() => setSapSubTab("parts")}
          >
            Parts
          </button>
          <button
            className={`${styles.subTab} ${sapSubTab === "metadata" ? styles.subActive : ""}`}
            onClick={() => setSapSubTab("metadata")}
          >
            Metadata
          </button>
        </div>
      )}

      <DataTable columns={columns} rows={rows} />
    </div>
  );
}

// Export for ActionBar to use the same logic
export function getTabData(results, activeTab) {
  if (activeTab === "cs") return results.cs_bom || [];
  if (activeTab === "bom") return results.bom_excel || [];
  if (activeTab === "sap") {
    const sap = results.sap_data || {};
    const parts = sap.parts
      ? Object.entries(sap.parts).map(([name, d]) => ({ name, ...d }))
      : [];
    const meta = sap.metadata
      ? Object.entries(sap.metadata).map(([key, value]) => ({ key, value }))
      : [];
    return [...parts, ...meta];
  }
  return [];
}
