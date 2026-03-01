import { useState } from "react";
import Header from "./components/Header/Header";
import Footer from "./components/Footer/Footer";
import UploadSection from "./components/UploadSection/UploadSection";
import ProgressIndicator from "./components/ProgressIndicator/ProgressIndicator";
import ResultsSection from "./components/ResultsSection/ResultsSection";
import { uploadDocuments, triggerExtraction } from "./api/client";
import styles from "./App.module.css";

export default function App() {
  const [step, setStep] = useState("upload"); // upload | extracting | results
  const [files, setFiles] = useState({ cs: null, bom: null, sap: null });
  const [identifier, setIdentifier] = useState(null);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  function handleFileChange(type, file) {
    setFiles((prev) => ({ ...prev, [type]: file }));
    setError(null);
  }

  async function handleExtract() {
    setError(null);
    setStep("extracting");

    try {
      const uploadRes = await uploadDocuments(files.cs, files.bom, files.sap);
      const id = uploadRes.identifier;
      setIdentifier(id);

      const extractRes = await triggerExtraction(id);
      setResults(extractRes.results);
      setStep("results");
    } catch (err) {
      setError(err.message || "Something went wrong");
      setStep("upload");
    }
  }

  function handleReset() {
    setStep("upload");
    setFiles({ cs: null, bom: null, sap: null });
    setIdentifier(null);
    setResults(null);
    setError(null);
  }

  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        {error && (
          <div className={styles.error}>
            <span>{error}</span>
            <button onClick={() => setError(null)}>&times;</button>
          </div>
        )}

        {step === "upload" && (
          <UploadSection
            files={files}
            onFileChange={handleFileChange}
            onExtract={handleExtract}
            loading={false}
          />
        )}

        {step === "extracting" && <ProgressIndicator />}

        {step === "results" && results && (
          <ResultsSection
            results={results}
            identifier={identifier}
            onReset={handleReset}
          />
        )}
      </main>
      <Footer />
    </div>
  );
}
