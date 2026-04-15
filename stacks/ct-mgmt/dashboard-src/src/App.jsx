import { sections, bookmarks } from './services';
import { useHealthChecks } from './useHealthChecks';
import { useResources } from './useResources';

function formatBytes(bytes) {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${bytes} B`;
}

function Bar({ value, className = '' }) {
  const color = value > 90 ? 'bg-red-400' : value > 70 ? 'bg-amber-400' : 'bg-neu-accent';
  return (
    <div className={`h-2 rounded-full bg-neu-dark shadow-neu-dot overflow-hidden ${className}`}>
      <div className={`h-full rounded-full ${color} transition-all duration-500`} style={{ width: `${value}%` }} />
    </div>
  );
}

/* ===== Top-level stats ===== */

function NodeCard({ node }) {
  return (
    <div className="p-4 bg-neu-bg rounded-neu shadow-neu-inset space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[0.7rem] font-bold uppercase tracking-widest text-neu-dim">{node.name}</h3>
        <span className="text-[0.6rem] text-neu-dim">{node.maxcpu} threads</span>
      </div>
      <div className="space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span className="text-neu-dim">CPU</span>
          <span className="text-neu-text font-medium">{node.cpu}%</span>
        </div>
        <Bar value={node.cpu} />
      </div>
      <div className="space-y-1">
        <div className="flex items-center justify-between text-xs">
          <span className="text-neu-dim">RAM</span>
          <span className="text-neu-text font-medium">{node.memPercent}%</span>
        </div>
        <Bar value={node.memPercent} />
        <span className="text-[0.6rem] text-neu-dim">{formatBytes(node.memUsed)} / {formatBytes(node.memTotal)}</span>
      </div>
    </div>
  );
}

function StorageCard({ storage }) {
  return (
    <div className="p-4 bg-neu-bg rounded-neu shadow-neu-inset space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-[0.7rem] font-bold uppercase tracking-widest text-neu-dim">{storage.name}</h3>
        <span className="text-[0.6rem] text-neu-dim">{storage.percent}% used</span>
      </div>
      <Bar value={storage.percent} />
      <div className="flex items-center justify-between">
        <span className="text-sm text-neu-text font-medium">{formatBytes(storage.free)} free</span>
        <span className="text-[0.6rem] text-neu-dim">of {formatBytes(storage.total)}</span>
      </div>
    </div>
  );
}

function SystemStats({ nodes, storage }) {
  if (!nodes.length && !storage.length) return null;
  return (
    <div className="space-y-4">
      {nodes.length > 0 && (
        <div className="grid grid-cols-2 gap-4">
          {nodes.map((n) => <NodeCard key={n.name} node={n} />)}
        </div>
      )}
      {storage.length > 0 && (
        <div className="grid grid-cols-2 gap-4">
          {storage.map((s) => <StorageCard key={s.name} storage={s} />)}
        </div>
      )}
    </div>
  );
}

/* ===== Service cards ===== */

function StatusDot({ up }) {
  if (up === undefined) return null;
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full shadow-neu-dot flex-shrink-0 ${
        up ? 'bg-green-400' : 'bg-red-400'
      }`}
    />
  );
}

function ServiceCard({ service, up }) {
  return (
    <a
      href={service.href}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center gap-3 p-4 bg-neu-bg rounded-neu shadow-neu-raised
                 hover:shadow-neu-hover hover:-translate-y-0.5
                 active:shadow-neu-pressed active:translate-y-0
                 transition-all duration-150"
    >
      <img
        src={service.icon}
        alt=""
        className="w-8 h-8 rounded flex-shrink-0"
        onError={(e) => { e.target.style.visibility = 'hidden'; }}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-neu-text text-sm truncate">{service.name}</span>
          <StatusDot up={up} />
        </div>
        <span className="text-xs text-neu-dim">{service.desc}</span>
      </div>
    </a>
  );
}

function SectionHeader({ children }) {
  return (
    <h2 className="text-[0.7rem] font-bold uppercase tracking-widest text-neu-dim mb-3">
      {children}
    </h2>
  );
}

function ServiceGrid({ section, health, gridClass, spanClass }) {
  return (
    <div>
      <SectionHeader>{section.name}</SectionHeader>
      <div className={`grid ${gridClass} gap-4`}>
        {section.services.map((svc, i) => (
          <div key={svc.name} className={spanClass && i === 0 ? spanClass : ''}>
            <ServiceCard service={svc} up={health[svc.ping]} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ===== App ===== */

export default function App({ initial = {} }) {
  const health = useHealthChecks(initial.health);
  const { nodes, storage } = useResources(initial.resources);

  const [quickAccess, often, infra, media] = sections;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8 space-y-8">
      <SystemStats nodes={nodes} storage={storage} />

      {/* Top row: Quick Access + Often side by side */}
      <div className="grid grid-cols-2 gap-8">
        <ServiceGrid section={quickAccess} health={health} gridClass="grid-cols-2" spanClass="col-span-2" />
        <ServiceGrid section={often} health={health} gridClass="grid-cols-2" spanClass="col-span-2" />
      </div>

      {/* Infrastructure: 3 columns */}
      <ServiceGrid section={infra} health={health} gridClass="grid-cols-3" />

      {/* Media Tools: 5 columns */}
      <ServiceGrid section={media} health={health} gridClass="grid-cols-5" />

      {/* Bookmarks */}
      <div>
        <SectionHeader>Bookmarks</SectionHeader>
        <div className="grid grid-cols-2 gap-8">
          {bookmarks.map((group) => (
            <div key={group.name}>
              <span className="text-xs uppercase tracking-wider text-neu-dim font-semibold">
                {group.name}
              </span>
              <div className="flex flex-wrap gap-3 mt-2">
                {group.links.map((link) => (
                  <a
                    key={link.name}
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 px-4 py-2 bg-neu-bg rounded-neu-sm
                               shadow-neu-sm hover:shadow-neu-sm-hover
                               hover:-translate-y-0.5 active:shadow-neu-pressed active:translate-y-0
                               transition-all duration-150 text-sm text-neu-text"
                  >
                    <img
                      src={link.icon}
                      alt=""
                      className="w-4 h-4"
                      onError={(e) => { e.target.style.visibility = 'hidden'; }}
                    />
                    {link.name}
                  </a>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
