'use client';

import { Fragment } from 'react';

import { ReviewTree } from '@/app/(app)/review/review-tree';

import { ClassificationIcon } from './icons/ClassificationIcon';

type MoveListProps = {
  tree: ReviewTree;
  activeNodeId: string;
  onNodeSelect: (nodeId: string) => void;
};

function movePrefix(ply: number): string {
  const number = Math.floor((ply - 1) / 2) + 1;
  return ply % 2 === 1 ? `${number}.` : `${number}...`;
}

export default function MoveList({
  tree,
  activeNodeId,
  onNodeSelect,
}: MoveListProps) {
  const mainline = tree.mainlineIds.slice(1);
  if (mainline.length === 0) {
    return null;
  }

  const pairs: { number: number; whiteId?: string; blackId?: string }[] = [];
  for (let i = 0; i < mainline.length; i += 2) {
    pairs.push({
      number: Math.floor(i / 2) + 1,
      whiteId: mainline[i],
      blackId: i + 1 < mainline.length ? mainline[i + 1] : undefined,
    });
  }

  return (
    <div className="max-h-48 overflow-y-auto rounded-2xl border border-black/40 bg-black/40 p-3 text-sm [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
      <div className="grid grid-cols-[2.25rem_1fr_1fr] gap-x-2 gap-y-1">
        {pairs.map((pair) => (
          <Fragment key={pair.number}>
            <span className="select-none self-center text-right text-xs font-medium text-white/50">
              {pair.number}.
            </span>
            <MoveButton
              tree={tree}
              nodeId={pair.whiteId}
              activeNodeId={activeNodeId}
              onNodeSelect={onNodeSelect}
            />
            <MoveButton
              tree={tree}
              nodeId={pair.blackId}
              activeNodeId={activeNodeId}
              onNodeSelect={onNodeSelect}
            />
            <VariationRows
              tree={tree}
              nodeId={pair.whiteId}
              activeNodeId={activeNodeId}
              onNodeSelect={onNodeSelect}
              depth={1}
            />
            <VariationRows
              tree={tree}
              nodeId={pair.blackId}
              activeNodeId={activeNodeId}
              onNodeSelect={onNodeSelect}
              depth={1}
            />
          </Fragment>
        ))}
        <VariationRows
          tree={tree}
          nodeId={tree.rootId}
          activeNodeId={activeNodeId}
          onNodeSelect={onNodeSelect}
          depth={1}
        />
      </div>
    </div>
  );
}

function VariationRows({
  tree,
  nodeId,
  activeNodeId,
  onNodeSelect,
  depth,
}: {
  tree: ReviewTree;
  nodeId?: string;
  activeNodeId: string;
  onNodeSelect: (nodeId: string) => void;
  depth: number;
}) {
  if (!nodeId) {
    return null;
  }
  const node = tree.nodes[nodeId];
  if (!node) {
    return null;
  }
  const isRoot = node.parentId === null;
  const variations = isRoot ? node.children.slice(1) : node.children;
  if (variations.length === 0) {
    return null;
  }

  return (
    <>
      {variations.map((childId) => {
        const child = tree.nodes[childId];
        return (
          <Fragment key={childId}>
            <div
              className="col-span-3 flex items-center gap-1.5"
              style={{ paddingLeft: `${depth * 14}px` }}
            >
              <span className="select-none font-mono text-[11px] text-white/40">
                {movePrefix(child.ply)}
              </span>
              <MoveButton
                tree={tree}
                nodeId={childId}
                activeNodeId={activeNodeId}
                onNodeSelect={onNodeSelect}
              />
            </div>
            <VariationRows
              tree={tree}
              nodeId={childId}
              activeNodeId={activeNodeId}
              onNodeSelect={onNodeSelect}
              depth={depth + 1}
            />
          </Fragment>
        );
      })}
    </>
  );
}

function MoveButton({
  tree,
  nodeId,
  activeNodeId,
  onNodeSelect,
}: {
  tree: ReviewTree;
  nodeId?: string;
  activeNodeId: string;
  onNodeSelect: (nodeId: string) => void;
}) {
  if (!nodeId) {
    return <span />;
  }
  const node = tree.nodes[nodeId];
  const move = node?.move;
  if (!move) {
    return <span />;
  }
  const isActive = nodeId === activeNodeId;

  return (
    <button
      type="button"
      onClick={() => onNodeSelect(nodeId)}
      className={`group inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm transition ${
        isActive
          ? 'bg-[#10b981]/15 text-[#10b981] ring-1 ring-[#10b981]/40'
          : 'text-white/90 hover:bg-white/5 hover:text-white'
      }`}
    >
      <ClassificationIcon classification={move.classification} size={13} />
      <span className="font-mono text-sm">{move.san}</span>
    </button>
  );
}
