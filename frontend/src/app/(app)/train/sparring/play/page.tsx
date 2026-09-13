import { redirect } from 'next/navigation';

import SparringGame from './SparringGame';
import { isPersonaKey, isPlayColor, isTierKey } from '../personas';

export default async function EngineSparringPlayPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const persona = typeof params.persona === 'string' ? params.persona : null;
  const tier = typeof params.tier === 'string' ? params.tier : null;
  const color = typeof params.color === 'string' ? params.color : null;

  if (!isPersonaKey(persona) || !isTierKey(tier) || !isPlayColor(color)) {
    redirect('/train/sparring');
  }

  return <SparringGame personaKey={persona} tierKey={tier} color={color} />;
}
