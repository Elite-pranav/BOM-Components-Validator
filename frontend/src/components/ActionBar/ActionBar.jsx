import { FiDownload } from "react-icons/fi";
import { downloadCsv } from "../../utils/exportCsv";
import { downloadExcel } from "../../utils/exportExcel";
import { getTabData } from "../DataTabs/DataTabs";
import styles from "./ActionBar.module.css";

const TAB_LABELS = { cs: "CS BOM", bom: "Excel BOM", sap: "SAP Data" };

export default function ActionBar({ results, activeTab, identifier }) {
  const data = getTabData(results, activeTab);
  const label = TAB_LABELS[activeTab] || "Data";

  return (
    <div className={styles.bar}>
      <span className={styles.info}>
        {data.length} rows &middot; {label}
      </span>
      <div className={styles.actions}>
        <button
          className={styles.btn}
          onClick={() => downloadCsv(data, `${identifier}_${activeTab}.csv`)}
          disabled={data.length === 0}
        >
          <FiDownload /> CSV
        </button>
        <button
          className={styles.btn}
          onClick={() =>
            downloadExcel(data, `${identifier}_${activeTab}.xlsx`, label)
          }
          disabled={data.length === 0}
        >
          <FiDownload /> Excel
        </button>
      </div>
    </div>
  );
}
