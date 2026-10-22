import { useQuery } from "@tanstack/react-query";
import { type OrderAcknowledgement } from "../services/api";

export function OrderAcknowledgementStatus() {
  const { data } = useQuery<OrderAcknowledgement & { total_ms: number }>({
    queryKey: ["last-order-ack"],
    enabled: false,
    staleTime: Infinity,
  });
  if (!data) return null;
  return <p role="status" className="rounded border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-200">
    Broker acknowledged order {data.order_id} in {data.total_ms} ms.
    {data.timing && <> Broker request: {data.timing.broker_ms} ms; local queue: {data.timing.queue_ms} ms.</>}
    {" "}Check Order Book for fill status.
  </p>;
}
