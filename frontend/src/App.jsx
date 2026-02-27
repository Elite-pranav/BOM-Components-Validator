import { useEffect, useMemo, useState } from 'react';

const JSON_TABS = ['bom', 'cs_bom', 'sap_data', 'sap_raw', 'comparison'];

function DataTable({ rows }) {
  if (!rows || rows.length === 0) return <p>No data</p>;
  const columns = Object.keys(rows[0]);

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx}>
              {columns.map((col) => (
                <td key={col}>{row[col] === null || row[col] === undefined ? '' : String(row[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [folders, setFolders] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [bomFile, setBomFile] = useState(null);
  const [sapFile, setSapFile] = useState(null);
  const [csFile, setCsFile] = useState(null);
  const [status, setStatus] = useState('Initializing...');
  const [results, setResults] = useState(null);
  const [activeTab, setActiveTab] = useState('bom');
  const [processing, setProcessing] = useState(false);

  const summary = useMemo(() => {
    if (!results) return null;
    return {
      folder: results.folder_id,
      bomRows: Array.isArray(results.bom) ? results.bom.length : 0,
      csRows: Array.isArray(results.cs_bom) ? results.cs_bom.length : 0,
      sapParts: results.sap_data?.parts ? Object.keys(results.sap_data.parts).length : 0,
      sapMetadata: results.sap_data?.metadata ? Object.keys(results.sap_data.metadata).length : 0,
      comparisonRows: Array.isArray(results.comparison?.comparison) ? results.comparison.comparison.length : 0,
    };
  }, [results]);

  async function loadFolders() {
    setStatus('Loading folders...');
    const response = await fetch('/api/folders');
    if (!response.ok) throw new Error('Failed to load folders');

    const data = await response.json();
    const folderItems = data.folders || [];
    setFolders(folderItems);

    if (!selectedFolder && folderItems.length > 0) {
      setSelectedFolder(folderItems[0].folder_id);
    }

    if (!sessionId && folderItems.length > 0) {
      setSessionId(folderItems[0].folder_id);
    }

    setStatus(`Loaded ${folderItems.length} folder(s)`);
  }

  async function loadResults(folderId = selectedFolder) {
    if (!folderId) {
      setStatus('No folder selected');
      return;
    }

    setStatus(`Loading results for ${folderId}...`);
    const response = await fetch(`/api/results/${encodeURIComponent(folderId)}`);
    if (!response.ok) {
      setResults(null);
      setStatus(`No processed output found for ${folderId}`);
      return;
    }

    const data = await response.json();
    setResults(data);
    if (data.comparison?.comparison) {
      setActiveTab('comparison');
    }
    setStatus(`Results loaded for ${folderId}`);
  }

  async function uploadAndExtract(type, file, id = sessionId) {
    if (!id) {
      setStatus('Session ID is required');
      return;
    }

    if (!file) {
      setStatus(`Please choose a file for ${type.toUpperCase()}`);
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`/api/extract/${type}/${encodeURIComponent(id)}`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`${type.toUpperCase()} extraction failed: ${errText}`);
    }
    return response.json();
  }

  async function runComparison(id = sessionId) {
    if (!id) {
      setStatus('Session ID is required');
      return;
    }

    const response = await fetch(`/api/compare/${encodeURIComponent(id)}`, {
      method: 'POST',
    });
    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Comparison failed: ${errText}`);
    }
    return response.json();
  }

  async function extractAllAndCompare() {
    if (!sessionId) {
      setStatus('Session ID is required');
      return;
    }
    if (!bomFile || !sapFile || !csFile) {
      setStatus('Please upload BOM, SAP, and CS files');
      return;
    }

    setProcessing(true);
    setStatus(`Extracting BOM for ${sessionId}...`);

    try {
      await uploadAndExtract('bom', bomFile, sessionId);
      setStatus(`Extracting SAP for ${sessionId}...`);
      await uploadAndExtract('sap', sapFile, sessionId);
      setStatus(`Extracting CS for ${sessionId}...`);
      await uploadAndExtract('cs', csFile, sessionId);
      setStatus(`Running abbreviation comparison for ${sessionId}...`);
      await runComparison(sessionId);
      setStatus(`Extraction and comparison complete for ${sessionId}`);
      await loadFolders();
      setSelectedFolder(sessionId);
      await loadResults(sessionId);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setProcessing(false);
    }
  }

  useEffect(() => {
    (async () => {
      try {
        await loadFolders();
      } catch (error) {
        setStatus(error.message);
      }
    })();
  }, []);

  useEffect(() => {
    if (selectedFolder) {
      loadResults(selectedFolder).catch((error) => setStatus(error.message));
    }
  }, [selectedFolder]);

  const sapPartRows = useMemo(() => {
    const partsObj = results?.sap_data?.parts || {};
    return Object.entries(partsObj).map(([part, value]) => ({
      part,
      raw: value.raw,
      material: value.material,
      coating: value.coating,
    }));
  }, [results]);

  const comparisonRows = useMemo(() => {
    return results?.comparison?.comparison || [];
  }, [results]);

  return (
    <main className="container">
      <header className="header">
        <h1>BOM Components Validator</h1>
        <p>Upload BOM, SAP, and CS files, extract data, and compare abbreviations.</p>
      </header>

      <section className="panel controls">
        <label htmlFor="sessionId">Session ID</label>
        <div className="row">
          <input
            id="sessionId"
            type="text"
            value={sessionId}
            onChange={(event) => setSessionId(event.target.value.trim())}
            placeholder="e.g. 81351387"
          />
        </div>

        <div className="row">
          <label>BOM (Excel)</label>
          <input type="file" accept=".xlsx,.xlsm,.xltx,.xltm" onChange={(e) => setBomFile(e.target.files?.[0] ?? null)} />
          <button
            onClick={async () => {
              try {
                setStatus(`Extracting BOM for ${sessionId}...`);
                await uploadAndExtract('bom', bomFile, sessionId);
                await loadFolders();
                setSelectedFolder(sessionId);
                await loadResults(sessionId);
                setStatus(`BOM extracted for ${sessionId}`);
              } catch (error) {
                setStatus(error.message);
              }
            }}
            disabled={processing}
          >
            Extract BOM
          </button>
        </div>

        <div className="row">
          <label>SAP (PDF)</label>
          <input type="file" accept=".pdf" onChange={(e) => setSapFile(e.target.files?.[0] ?? null)} />
          <button
            onClick={async () => {
              try {
                setStatus(`Extracting SAP for ${sessionId}...`);
                await uploadAndExtract('sap', sapFile, sessionId);
                await loadFolders();
                setSelectedFolder(sessionId);
                await loadResults(sessionId);
                setStatus(`SAP extracted for ${sessionId}`);
              } catch (error) {
                setStatus(error.message);
              }
            }}
            disabled={processing}
          >
            Extract SAP
          </button>
        </div>

        <div className="row">
          <label>CS (PDF)</label>
          <input type="file" accept=".pdf" onChange={(e) => setCsFile(e.target.files?.[0] ?? null)} />
          <button
            onClick={async () => {
              try {
                setStatus(`Extracting CS for ${sessionId}...`);
                await uploadAndExtract('cs', csFile, sessionId);
                await loadFolders();
                setSelectedFolder(sessionId);
                await loadResults(sessionId);
                setStatus(`CS extracted for ${sessionId}`);
              } catch (error) {
                setStatus(error.message);
              }
            }}
            disabled={processing}
          >
            Extract CS
          </button>
        </div>

        <div className="row">
          <button className="primary" onClick={extractAllAndCompare} disabled={processing}>
            {processing ? 'Running...' : 'Extract All + Compare'}
          </button>
          <button
            onClick={async () => {
              try {
                setStatus(`Running comparison for ${sessionId}...`);
                await runComparison(sessionId);
                await loadResults(sessionId);
                setStatus(`Comparison complete for ${sessionId}`);
              } catch (error) {
                setStatus(error.message);
              }
            }}
            disabled={processing}
          >
            Run Comparison
          </button>
        </div>
        <p className="status">{status}</p>
      </section>

      <section className="panel controls">
        <label htmlFor="folderSelect">Folder ID</label>
        <div className="row">
          <select
            id="folderSelect"
            value={selectedFolder}
            onChange={(event) => setSelectedFolder(event.target.value)}
          >
            {folders.map((item) => (
              <option key={item.folder_id} value={item.folder_id}>
                {item.processed ? `${item.folder_id} (processed)` : item.folder_id}
              </option>
            ))}
          </select>
          <button onClick={() => loadFolders().catch((error) => setStatus(error.message))}>
            Refresh
          </button>
          <button onClick={() => loadResults().catch((error) => setStatus(error.message))}>
            Load Results
          </button>
        </div>
      </section>

      <section className="panel summary">
        <h2>Summary</h2>
        <div className="summary-grid">
          <div className="card">
            <strong>Folder</strong>
            <div>{summary?.folder ?? '-'}</div>
          </div>
          <div className="card">
            <strong>BOM rows</strong>
            <div>{summary?.bomRows ?? 0}</div>
          </div>
          <div className="card">
            <strong>CS rows</strong>
            <div>{summary?.csRows ?? 0}</div>
          </div>
          <div className="card">
            <strong>SAP parts</strong>
            <div>{summary?.sapParts ?? 0}</div>
          </div>
          <div className="card">
            <strong>SAP metadata fields</strong>
            <div>{summary?.sapMetadata ?? 0}</div>
          </div>
          <div className="card">
            <strong>Comparison rows</strong>
            <div>{summary?.comparisonRows ?? 0}</div>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>BOM (Excel)</h2>
        <DataTable rows={results?.bom || []} />
      </section>

      <section className="panel">
        <h2>CS BOM (Drawing)</h2>
        <DataTable rows={results?.cs_bom || []} />
      </section>

      <section className="panel">
        <h2>SAP Parts</h2>
        <DataTable rows={sapPartRows} />
      </section>

      <section className="panel">
        <h2>Abbreviation Comparison</h2>
        <DataTable rows={comparisonRows} />
      </section>

      <section className="panel">
        <h2>Raw JSON</h2>
        <div className="tabs">
          {JSON_TABS.map((tab) => (
            <button
              key={tab}
              className={`tab ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab}.json
            </button>
          ))}
        </div>
        <pre>{JSON.stringify(results?.[activeTab] ?? null, null, 2)}</pre>
      </section>
    </main>
  );
}
