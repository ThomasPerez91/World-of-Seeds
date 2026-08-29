import { BackIcon } from "./icons";
import { useI18n } from "../i18n";

export type LegalDocument = "legal" | "terms";

export function LegalLinks({
  onOpen,
}: {
  onOpen: (document: LegalDocument) => void;
}) {
  const { t } = useI18n();
  return (
    <nav className="legal-links" aria-label={t("legal.links")}>
      <button type="button" onClick={() => onOpen("legal")}>
        {t("legal.notices")}
      </button>
      <button type="button" onClick={() => onOpen("terms")}>
        {t("legal.terms")}
      </button>
    </nav>
  );
}

function LegalNotice() {
  return (
    <>
      <p className="eyebrow">Informations</p>
      <h1 id="legal-document-title">Mentions légales et vie privée</h1>

      <section>
        <h2>Édition</h2>
        <p>
          World of Seeds est un service numérique privé, personnel et non commercial,
          accessible uniquement sur invitation. L’éditeur agit à titre non professionnel et
          conserve son anonymat. Pour toute demande, contacte l’administrateur qui t’a transmis
          tes identifiants.
        </p>
      </section>

      <section>
        <h2>Hébergement</h2>
        <p>
          Le service est hébergé par OVH SAS, société immatriculée au RCS de Lille Métropole
          sous le numéro 424 761 419, dont le siège social est situé 2 rue Kellermann, 59100
          Roubaix, France.
        </p>
        <a href="https://www.ovhcloud.com/fr/terms-and-conditions/" target="_blank" rel="noreferrer">
          Mentions légales d’OVHcloud
        </a>
      </section>

      <section>
        <h2>Données traitées</h2>
        <p>
          Le service traite uniquement les données nécessaires à la gestion des accès, à la
          sécurité et aux opérations demandées : nom d’utilisateur, empreinte du mot de passe,
          état du compte, sessions, compteurs anti-abus et métadonnées de corbeille. Les mots de
          passe et jetons de session ne sont pas conservés en clair.
        </p>
        <p>
          Ces informations sont accessibles à l’administrateur du service et sont hébergées sur
          son infrastructure OVH. Elles sont conservées tant que l’accès est actif. Après
          désactivation, certaines métadonnées peuvent rester archivées le temps nécessaire à la
          sécurité, à l’intégrité des fichiers et à leur purge technique.
        </p>
      </section>

      <section>
        <h2>Cookies et sécurité</h2>
        <p>
          Deux cookies strictement nécessaires assurent la session et la protection CSRF. Aucun
          cookie publicitaire, outil de profilage ou mesure d’audience tierce n’est utilisé. Le
          blocage de ces cookies techniques empêche la connexion.
        </p>
      </section>

      <section>
        <h2>Tes droits</h2>
        <p>
          Tu peux demander l’accès, la rectification, la limitation ou l’effacement de tes données
          en contactant l’administrateur qui t’a remis l’accès. Tu peux également introduire une
          réclamation auprès de la CNIL.
        </p>
        <a href="https://www.cnil.fr/fr/comprendre-mes-droits" target="_blank" rel="noreferrer">
          Comprendre tes droits auprès de la CNIL
        </a>
      </section>

      <p className="legal-updated">Dernière mise à jour : 15 août 2026.</p>
    </>
  );
}

function TermsOfUse() {
  return (
    <>
      <p className="eyebrow">Accès privé</p>
      <h1 id="legal-document-title">Conditions d’utilisation</h1>

      <section>
        <h2>Objet</h2>
        <p>
          Le service permet à un cercle restreint d’utilisateurs autorisés de gérer et récupérer
          les fichiers de leur espace personnel. L’accès est gratuit, révocable et ne constitue
          ni une offre commerciale ni un service ouvert au public.
        </p>
      </section>

      <section>
        <h2>Accès au compte</h2>
        <p>
          Les identifiants sont personnels et confidentiels. Ils ne doivent pas être partagés.
          Toute activité réalisée depuis un compte est réputée provenir de son titulaire. En cas
          de doute, change immédiatement ton mot de passe et préviens l’administrateur.
        </p>
      </section>

      <section>
        <h2>Usages autorisés</h2>
        <p>
          Tu dois utiliser le service dans le respect de la loi, des droits d’auteur, de la vie
          privée et des droits des tiers. Tu ne dois déposer, récupérer ou partager que des
          contenus que tu as le droit d’utiliser.
        </p>
      </section>

      <section>
        <h2>Usages interdits</h2>
        <p>
          Sont notamment interdits : le partage d’accès, les contenus illicites, les tentatives de
          contournement de l’isolation des comptes, l’exploration du serveur, les attaques, les
          automatismes abusifs et toute consommation de ressources susceptible de dégrader le
          service ou les espaces des autres utilisateurs.
        </p>
      </section>

      <section>
        <h2>Fichiers et disponibilité</h2>
        <p>
          Les actions de renommage, déplacement, restauration et suppression sont exécutées à ta
          demande. Vérifie la destination et conserve une copie des fichiers importants. Le
          service peut être interrompu pour maintenance et aucune disponibilité permanente n’est
          garantie.
        </p>
      </section>

      <section>
        <h2>Administration</h2>
        <p>
          L’administrateur peut suspendre ou retirer un accès en cas de risque, d’abus ou de
          non-respect de ces conditions. Les conditions peuvent évoluer avec les fonctionnalités ;
          la version à jour reste accessible depuis l’interface.
        </p>
      </section>

      <p className="legal-updated">Dernière mise à jour : 15 août 2026.</p>
    </>
  );
}

export function LegalPage({
  document,
  onBack,
  onOpen,
}: {
  document: LegalDocument;
  onBack: () => void;
  onOpen: (document: LegalDocument) => void;
}) {
  const { t } = useI18n();
  return (
    <main className="legal-page">
      <header className="legal-page-header">
        <button type="button" className="back-button" onClick={onBack}>
          <BackIcon /> {t("legal.back")}
        </button>
        <LegalLinks onOpen={onOpen} />
      </header>
      <article className="legal-document" aria-labelledby="legal-document-title">
        {document === "legal" ? <LegalNotice /> : <TermsOfUse />}
      </article>
    </main>
  );
}
