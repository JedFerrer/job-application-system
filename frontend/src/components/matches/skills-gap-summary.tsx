"use client";

import { useQuery } from "@tanstack/react-query";
import { TrendingUp } from "lucide-react";
import { matchesApi } from "@/lib/api";
import { formatSkillLabel } from "@/lib/skill-display";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Surfaces the skills that most frequently block the user's matched jobs. */
export function SkillsGapSummary() {
  const gapQuery = useQuery({
    queryKey: ["skills-gap-summary"],
    queryFn: () => matchesApi.skillsGapSummary(8),
  });

  if (gapQuery.isLoading || !gapQuery.data || gapQuery.data.items.length === 0) {
    return null;
  }

  const max = gapQuery.data.items[0]?.frequency ?? 1;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <TrendingUp className="size-4" />
          Top skill gaps
        </CardTitle>
        <CardDescription>
          Most common missing keywords across{" "}
          {gapQuery.data.total_matches_considered} matched jobs — closing these
          widens your matches.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        {gapQuery.data.items.map((item) => (
          <Badge
            key={item.keyword}
            variant={item.frequency >= max * 0.66 ? "default" : "secondary"}
            className="gap-1"
          >
            {formatSkillLabel(item.keyword)}
            <span className="text-xs opacity-70">×{item.frequency}</span>
          </Badge>
        ))}
      </CardContent>
    </Card>
  );
}
