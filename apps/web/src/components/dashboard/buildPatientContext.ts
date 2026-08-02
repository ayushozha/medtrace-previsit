import type { ClinicalSnapshot } from '@/lib/types';

/** Compact read-only chart summary for the dashboard collaboration agent. */
export function buildPatientContext(snapshot: ClinicalSnapshot): string {
  const { patient } = snapshot;
  const lines: string[] = [
    `Patient: ${patient.name} (${patient.age}Y ${patient.sex})`,
    `Primary doctor: ${patient.primary_doctor ?? 'unassigned'}`,
    `Last visit: ${patient.last_visit ?? 'unknown'}`,
  ];

  if (snapshot.active_conditions.length) {
    lines.push(
      'Conditions: ' +
        snapshot.active_conditions.map((c) => c.name).join(', '),
    );
  }
  const activeMeds = snapshot.current_medications.filter((m) => m.status === 'Active');
  if (activeMeds.length) {
    lines.push('Medications: ' + activeMeds.map((m) => m.name).join(', '));
  }
  if (snapshot.allergies.length) {
    lines.push('Allergies: ' + snapshot.allergies.map((a) => a.allergen).join(', '));
  }
  if (snapshot.lab_trends.length) {
    lines.push(
      'Labs: ' +
        snapshot.lab_trends
          .slice(0, 8)
          .map((l) => `${l.test}=${l.latest} (${l.status}/${l.trend})`)
          .join('; '),
    );
  }
  if (snapshot.risk_alerts.length) {
    lines.push(
      'Alerts: ' + snapshot.risk_alerts.map((a) => `[${a.priority}] ${a.message}`).join('; '),
    );
  }
  if (snapshot.doctor_checklist.length) {
    lines.push('Suggested checklist: ' + snapshot.doctor_checklist.join(' | '));
  }

  return lines.join('\n');
}
