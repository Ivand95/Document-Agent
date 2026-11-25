import { useAdmin } from "../adminContext";
import { FoldersSection } from "./FoldersSection";
import { ReportSection } from "./ReportSection";

export const IngestionPage = () => {
  const { token } = useAdmin();
  return (
    <>
      <FoldersSection token={token} />
      <ReportSection token={token} />
    </>
  );
};
