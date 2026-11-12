"use client";
import { useParams } from "next/navigation";
import ReportEditor from "../../../../components/ReportEditor";
export default function EditReport() { const { id } = useParams<{ id: string }>(); return <ReportEditor reportId={id} />; }
