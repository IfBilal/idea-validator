// Delivery pipeline (Guide §8.2). Runs after merge to main (SCM polling) and nightly (evaluation).
// Credentials: 'kubeconfig-kind' (secret file, exposed as CI_KUBECONFIG; scripts/lib/kube_guard.sh refuses any
// context that is not kind-idea-validator). Never commit kubeconfig. Jenkins itself: scripts/jenkins_up.sh.
pipeline {
  agent any
  options { timestamps(); disableConcurrentBuilds(); timeout(time: 60, unit: 'MINUTES') }
  triggers {
    pollSCM('H/2 * * * *')   // webhook cannot reach a laptop
    cron('H 2 * * *')        // nightly full evaluation
  }
  environment {
    REGISTRY = "localhost:5001"
    NS = "idea-validator"
  }
  stages {
    stage('Checkout') {
      steps {
        checkout scm
        script { env.TAG = sh(returnStdout: true, script: 'git rev-parse --short=7 HEAD').trim() }   // also works on non-SCM runs
      }
    }
    stage('Build')    { steps { sh 'make images TAG=$TAG' } }
    stage('Push')     { steps { sh './scripts/push_images.sh $TAG' } }
    stage('Deploy') {
      steps {
        withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
          sh './scripts/deploy.sh $TAG'          // kustomize edit set image + apply + rollout status (timeout)
        }
      }
    }
    stage('Smoke') {
      steps { withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) { sh './scripts/smoke_test.sh' } }
    }
    stage('Eval') {
      when { triggeredBy 'TimerTrigger' }
      steps {
        withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
          sh './scripts/run_eval.sh'   // guarded: apply the eval job, then compare with the stored baseline
        }
      }
    }
  }
  post {
    failure {
      echo 'Pipeline failed, nothing promoted'
      script {
        if (env.TAG) {   // unset when checkout itself failed: nothing was deployed
          // a failed rollback must stay visible: it marks the build, it is never swallowed
          catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
            withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
              sh './scripts/rollback_changed.sh $TAG'   // rollout undo for deployments changed in this build
            }
          }
        }
      }
    }
  }
}
